#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROFILE_DIR="$SCRIPT_DIR/apparmor"
SECCOMP_OUT="$SCRIPT_DIR/seccomp-bwrap.json"
TMP="$(mktemp)"
trap 'rm -f "$TMP" "${SECCOMP_OUT}.tmp"' EXIT

MOBY_PROFILES_COMMIT="7b5ccfab2d6b4e0aa64c6cce401a2705788eafeb"
UPSTREAM_URL="https://raw.githubusercontent.com/moby/profiles/$MOBY_PROFILES_COMMIT/seccomp/default.json"

for command in curl jq apparmor_parser aa-status; do
  command -v "$command" >/dev/null 2>&1 || {
    printf 'Required host tool is missing: %s\n' "$command" >&2
    exit 1
  }
done

curl --fail --silent --show-error --location --retry 3 "$UPSTREAM_URL" -o "$TMP"
jq -e '
  .defaultAction == "SCMP_ACT_ERRNO"
  and (.syscalls | type == "array")
' "$TMP" >/dev/null

# Extend the pinned Docker/Moby allowlist only with namespace and mount
# syscalls needed by Bubblewrap. AppArmor is the second mandatory layer:
# the trusted Runner profile can use them; the workload profile cannot.
jq '
  .syscalls = (
    [{
      "names": [
        "clone", "clone3", "unshare",
        "mount", "mount_setattr", "open_tree", "move_mount",
        "fsopen", "fsconfig", "fsmount", "fspick",
        "umount", "umount2", "pivot_root",
        "sethostname", "setdomainname", "chroot"
      ],
      "action": "SCMP_ACT_ALLOW",
      "comment": "Developer OS Bubblewrap setup only; AppArmor workload profile denies userns, mount, pivot_root and capabilities after shell transition."
    }] + .syscalls
  )
' "$TMP" > "${SECCOMP_OUT}.tmp"

jq -e '
  .defaultAction == "SCMP_ACT_ERRNO"
  and (.syscalls[0].names | index("clone") != null)
  and (.syscalls[0].names | index("pivot_root") != null)
' "${SECCOMP_OUT}.tmp" >/dev/null
mv "${SECCOMP_OUT}.tmp" "$SECCOMP_OUT"

# AppArmor profile load is host-scoped; never relax the system-wide userns policy.
apparmor_parser -r -W "$PROFILE_DIR/developer-os-runner"
aa-status | grep -q 'developer-os-runner'

printf 'Prepared strict Runner security profiles.\n'
printf '  AppArmor: developer-os-runner\n'
printf '  Seccomp:  %s\n' "$SECCOMP_OUT"
printf '  Moby source commit: %s\n' "$MOBY_PROFILES_COMMIT"
