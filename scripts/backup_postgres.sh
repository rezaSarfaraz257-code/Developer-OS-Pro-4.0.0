#!/usr/bin/env sh
set -eu
umask 077

: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"

OUT_DIR="${BACKUP_DIR:-./backups}"
mkdir -p "$OUT_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FINAL_FILE="$OUT_DIR/developer-os-$STAMP-$$.dump"
TEMP_FILE="$(mktemp "$OUT_DIR/.developer-os-$STAMP.XXXXXX")"
CHECKSUM_TEMP_FILE=""
CHECKSUM_FILE="$FINAL_FILE.sha256"
FINAL_PUBLISHED=0
CHECKSUM_PUBLISHED=0

cleanup() {
  rm -f "$TEMP_FILE"
  if [ -n "$CHECKSUM_TEMP_FILE" ]; then
    rm -f "$CHECKSUM_TEMP_FILE"
  fi
  # A dump without its matching checksum is not a usable recovery artifact.
  # Remove only the final dump created by this process; never touch a
  # pre-existing checksum or another backup.
  if [ "$FINAL_PUBLISHED" -eq 1 ] && [ "$CHECKSUM_PUBLISHED" -ne 1 ]; then
    rm -f "$FINAL_FILE"
  fi
  unset PGPASSWORD PGUSER
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

CHECKSUM_TEMP_FILE="$(mktemp "$OUT_DIR/.developer-os-$STAMP.checksum.XXXXXX")"

export PGPASSWORD="$POSTGRES_PASSWORD"
export PGUSER="$POSTGRES_USER"

# Write to a private temporary file first so an interrupted dump can never be
# mistaken for a valid recovery artifact. Custom format supports pg_restore.
pg_dump \
  --format=custom \
  --no-owner \
  --no-acl \
  --file="$TEMP_FILE" \
  "$POSTGRES_DB"
chmod 600 "$TEMP_FILE"

# Publish without replacing an earlier backup if two jobs collide or a name
# already exists. The temporary file is in the same directory/filesystem.
if ! ln "$TEMP_FILE" "$FINAL_FILE"; then
  echo "Refusing to overwrite existing backup: $FINAL_FILE" >&2
  exit 1
fi
FINAL_PUBLISHED=1
rm -f "$TEMP_FILE"

# Prepare the checksum privately, then publish it without replacing an
# existing sidecar. The dump is complete only after both links succeed.
(
  cd "$OUT_DIR"
  sha256sum "./$(basename "$FINAL_FILE")"
) > "$CHECKSUM_TEMP_FILE"
chmod 600 "$CHECKSUM_TEMP_FILE"
if ! ln "$CHECKSUM_TEMP_FILE" "$CHECKSUM_FILE"; then
  echo "Refusing to overwrite existing backup checksum: $CHECKSUM_FILE" >&2
  exit 1
fi
CHECKSUM_PUBLISHED=1
rm -f "$CHECKSUM_TEMP_FILE"
CHECKSUM_TEMP_FILE=""

printf '%s\n' "Created $FINAL_FILE"
printf '%s\n' "Checksum: $CHECKSUM_FILE"
