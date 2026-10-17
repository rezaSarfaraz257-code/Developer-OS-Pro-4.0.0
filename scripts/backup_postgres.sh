#!/usr/bin/env sh
set -eu
umask 077

: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"

OUT_DIR="${BACKUP_DIR:-./backups}"
mkdir -p "$OUT_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FINAL_FILE="$OUT_DIR/developer-os-$STAMP.dump"
TEMP_FILE="$FINAL_FILE.tmp.$$"
CHECKSUM_FILE="$FINAL_FILE.sha256"

cleanup() {
  rm -f "$TEMP_FILE"
  unset PGPASSWORD PGUSER
}
trap cleanup EXIT HUP INT TERM

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

# Publish atomically only after pg_dump exits successfully.
mv -f "$TEMP_FILE" "$FINAL_FILE"
chmod 600 "$FINAL_FILE"

# Store a detached checksum beside the artifact for transfer/storage integrity
# verification before a restore. Encryption remains the responsibility of the
# configured backup storage/KMS layer rather than this script.
sha256sum "$FINAL_FILE" > "$CHECKSUM_FILE"
chmod 600 "$CHECKSUM_FILE"

printf '%s\n' "Created $FINAL_FILE"
printf '%s\n' "Checksum: $CHECKSUM_FILE"
