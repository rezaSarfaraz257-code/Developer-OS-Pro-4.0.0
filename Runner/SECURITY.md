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

The profile has two security domains:

- `developer-os-runner`: trusted control-plane and Bubblewrap setup process. It can create the unprivileged namespaces and mounts required to construct the sandbox; Docker still drops every container capability and keeps no-new-privileges enabled.
- `developer-os-runner-workload`: the executed shell and all descendants. The shell transitions here after Bubblewrap setup; this profile has no user-namespace, mount, pivot-root, or capability permission.

Keep Docker `cap_drop: ALL`, `no-new-privileges:true`, the generated seccomp profile,
read-only root filesystem, resource limits, and Bubblewrap network isolation enabled.
Do not use `seccomp=unconfined` or `apparmor=unconfined`. If the profile cannot
be loaded or Bubblewrap cannot establish its namespaces, strict execution must
remain unavailable; never switch to an unsandboxed fallback.

This setup requires a host with AppArmor 4.0 user-namespace mediation and Docker
support for custom seccomp profiles. If the host cannot load the profile or
apply the generated seccomp policy, do not enable Runner execution on that host.
