# Runner host security setup

The Runner uses Bubblewrap in strict mode. On Ubuntu 24.04 and newer, AppArmor
restricts unprivileged user namespaces unless the launching profile explicitly
permits them. Do **not** disable `kernel.apparmor_restrict_unprivileged_userns`
system-wide and do not run the Runner with `apparmor=unconfined`.

Prepare the Docker host before starting the Runner:

```bash
sudo apt-get install -y apparmor apparmor-utils jq curl
sudo bash Runner/prepare-host.sh
```

The Runner image builds Bubblewrap 0.13.1 from the upstream signed release source archive, verifies its SHA-256 digest, and installs the non-setuid binary. This avoids relying on the older 0.8.0 package shipped by Debian Bookworm. The image build also checks the installed version and rejects a setuid bit.

The preparation script:
- Loads the scoped `developer-os-runner` AppArmor profile.
- Requires Docker `systempaths=unconfined` for the Runner container because Docker's default masked procfs submounts prevent Bubblewrap from mounting a new procfs for its isolated PID namespace. This option also removes Docker's default procfs/sysfs path masks, so the attached AppArmor policy is mandatory and explicitly denies the sensitive paths before the Runner or workload can access them. This is **not** permission to access those paths: the AppArmor setup profile and its stacked workload profile explicitly deny access to the Docker-masked procfs/sysfs paths, and CI probes those denials before running the real integration tests.
- CI enumerates and probes the Docker default masked/read-only system paths (including `/proc/kcore`, `/proc/keys`, `/proc/sys`, `/proc/sysrq-trigger`, `/sys/firmware`, and `/sys/fs/selinux`) under the attached AppArmor profile; the real workspace workload repeats the sensitive-path checks after Bubblewrap creates its private PID namespace. The container-level probe runs as UID 0 with zero effective capabilities and `no-new-privileges`, so ordinary file ownership cannot masquerade as an AppArmor denial.
- Allows Bubblewrap's exact recursive bind of `/oldroot/proc/` into the sandbox `/proc/` during setup; this does not grant mount permissions to the workload profile.
- Generates `Runner/seccomp-bwrap.json` from the Docker/Moby default seccomp allowlist pinned to commit `7b5ccfab2d6b4e0aa64c6cce401a2705788eafeb`.
- Adds only the namespace and mount syscalls required by Bubblewrap. The AppArmor workload profile is the mandatory second layer and denies those operations after the shell transition.

Set the following in the host's Compose environment (or `.env`):

```dotenv
RUNNER_APPARMOR_PROFILE=developer-os-runner
```

## Agent task authorization

- Every newly generated AgentTask must carry 1–6 task-specific, bounded acceptance criteria. Run-level criteria are supplementary and cannot replace a task's own criteria.
- A patch intended for an AgentTask must be requested with both `agent_run_id` and `agent_task_id`. The signed approval token binds the user, workspace, exact revision, run, task, and proposed patch. Supplying only one task identifier produces no approval token.
- Patch application re-locks and revalidates the run, task, dependency DAG, and workspace revision. The resulting audit record stores the exact run/task IDs.
- Verification queueing and worker execution both require that exact task-bound audit record. A workspace-only AI patch approval cannot be reassigned to an AgentTask.

The policy uses two profiles:

- `developer-os-runner`: trusted control-plane and Bubblewrap setup process. It has only the namespace, mount, and setup capabilities required to construct the sandbox; Docker still drops every container capability and keeps no-new-privileges enabled.
- `developer-os-runner-workload`: stacked with the setup profile when Bubblewrap executes the workspace shell. AppArmor enforces the intersection of both profiles, so the child cannot inherit the setup-only capabilities or mount, unmount, or pivot-root permissions. The child profile explicitly applies `deny userns`, so the workload cannot create nested user namespaces even though the trusted Bubblewrap setup profile is permitted to create the namespaces needed for isolation.

Bubblewrap sets `no_new_privs` before executing the workspace command, which prevents a normal AppArmor profile transition. The explicit path-scoped `allow pix` rules for `/usr/bin/bash` and `/bin/bash` use an AppArmor stack instead: it only adds restrictions, so `no_new_privs` remains enabled throughout execution. The profile pins AppArmor ABI 4.0 for supported Ubuntu LTS hosts. The workspace command environment is cleared by Bubblewrap before the shell starts.

Keep Docker `cap_drop: ALL`, `no-new-privileges:true`, the generated seccomp profile,
read-only root filesystem, resource limits, and Bubblewrap network isolation enabled.
Do not use `seccomp=unconfined` or `apparmor=unconfined`. If the profile cannot
be loaded or Bubblewrap cannot establish its namespaces, strict execution must
remain unavailable; never switch to an unsandboxed fallback.

This setup requires a host with AppArmor 4.0 user-namespace mediation and Docker
support for custom seccomp profiles. If the host cannot load the profile or
apply the generated seccomp policy, do not enable Runner execution on that host.
