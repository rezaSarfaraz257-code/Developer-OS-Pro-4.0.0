# Runner host security setup

The Runner uses Bubblewrap in strict mode. On Ubuntu 24.04 and newer, AppArmor
restricts unprivileged user namespaces unless the launching profile explicitly
permits them. Do **not** disable `kernel.apparmor_restrict_unprivileged_userns`
system-wide and do not run the Runner with `apparmor=unconfined`.

Load the narrow Runner profile on the Docker host before starting the service:

```bash
sudo apt-get install -y apparmor apparmor-utils
sudo apparmor_parser -r -W Runner/apparmor/developer-os-runner
sudo aa-status | grep developer-os-runner
```

Then set the following in the host's Compose environment (or `.env`):

```dotenv
RUNNER_APPARMOR_PROFILE=developer-os-runner
```

The profile has two security domains:

- `developer-os-runner`: trusted control-plane and Bubblewrap setup process. It can create the unprivileged namespaces and mounts required to construct the sandbox; Docker still drops every container capability and keeps no-new-privileges enabled.
- `developer-os-runner-workload`: the executed shell and all descendants. The shell transitions here after Bubblewrap setup; this profile has no user-namespace, mount, or capability permission.

Keep Docker `cap_drop: ALL`, `no-new-privileges:true`, read-only root filesystem,
resource limits, and Bubblewrap network isolation enabled. If the profile cannot
be loaded or Bubblewrap cannot establish its namespaces, strict execution must
remain unavailable; never switch to an unsandboxed fallback.

On hosts without AppArmor 4.0 user-namespace mediation, leave
`RUNNER_APPARMOR_PROFILE` unset only if the host's default profile already
supports the required Bubblewrap namespaces. Verify with the Runner's strict
capability probe before enabling execution.
