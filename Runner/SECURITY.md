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

The preparation script:
- Loads the scoped `developer-os-runner` AppArmor profile.
- Generates `Runner/seccomp-bwrap.json` from the Docker/Moby default seccomp allowlist pinned to commit `7b5ccfab2d6b4e0aa64c6cce401a2705788eafeb`.
- Adds only the namespace and mount syscalls required by Bubblewrap. The AppArmor workload profile is the mandatory second layer and denies those operations after the shell transition.

Set the following in the host's Compose environment (or `.env`):

```dotenv
RUNNER_APPARMOR_PROFILE=developer-os-runner
```

The policy uses two profiles:

- `developer-os-runner`: trusted control-plane and Bubblewrap setup process. It has only the namespace, mount, and setup capabilities required to construct the sandbox; Docker still drops every container capability and keeps no-new-privileges enabled.
- `developer-os-runner-workload`: stacked with the setup profile when Bubblewrap executes the workspace shell. AppArmor enforces the intersection of both profiles, so the child cannot inherit the setup-only capabilities or mount, unmount, or pivot-root permissions. The child profile grants no user-namespace permission.

Bubblewrap sets `no_new_privs` before executing the workspace command, which prevents a normal AppArmor profile transition. The `px -> &developer-os-runner-workload` rule uses an AppArmor stack instead: it only adds restrictions, so `no_new_privs` remains enabled throughout execution. The workspace command environment is cleared by Bubblewrap before the shell starts.

Keep Docker `cap_drop: ALL`, `no-new-privileges:true`, the generated seccomp profile,
read-only root filesystem, resource limits, and Bubblewrap network isolation enabled.
Do not use `seccomp=unconfined` or `apparmor=unconfined`. If the profile cannot
be loaded or Bubblewrap cannot establish its namespaces, strict execution must
remain unavailable; never switch to an unsandboxed fallback.

This setup requires a host with AppArmor 4.0 user-namespace mediation and Docker
support for custom seccomp profiles. If the host cannot load the profile or
apply the generated seccomp policy, do not enable Runner execution on that host.
