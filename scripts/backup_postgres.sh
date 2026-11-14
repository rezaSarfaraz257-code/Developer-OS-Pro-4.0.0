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
CHECKSUM_FILE="$FINAL_FILE.sha256"

cleanup() {
  rm -f "$TEMP_FILE"
  unset PGPASSWORD PGUSER
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

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
rm -f "$TEMP_FILE"

# Store a portable checksum relative to the backup directory. The dump and
# checksum can be moved together to isolated recovery storage.
(
  cd "$OUT_DIR"
  sha256sum "./$(basename "$FINAL_FILE")"
) > "$CHECKSUM_FILE"
chmod 600 "$CHECKSUM_FILE"

printf '%s\n' "Created $FINAL_FILE"
printf '%s\n' "Checksum: $CHECKSUM_FILE"
