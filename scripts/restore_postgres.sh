#!/usr/bin/env sh
set -eu
umask 077

: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
: "${BACKUP_FILE:?BACKUP_FILE must point to a pg_dump custom-format file}"

if [ ! -f "$BACKUP_FILE" ]; then
  echo "Backup file not found: $BACKUP_FILE" >&2
  exit 1
fi

BACKUP_DIR="$(dirname "$BACKUP_FILE")"
BACKUP_NAME="$(basename "$BACKUP_FILE")"
CHECKSUM_NAME="$BACKUP_NAME.sha256"
if [ ! -f "$BACKUP_DIR/$CHECKSUM_NAME" ]; then
  echo "Backup checksum file not found: $BACKUP_DIR/$CHECKSUM_NAME" >&2
  exit 1
fi

cleanup() {
  unset PGPASSWORD PGUSER
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

export PGPASSWORD="$POSTGRES_PASSWORD"
export PGUSER="$POSTGRES_USER"

(
  cd "$BACKUP_DIR"
  # Fail closed on missing/corrupt checksum or an invalid custom-format archive.
  sha256sum -c "$CHECKSUM_NAME"
  pg_restore --list "$BACKUP_NAME" >/dev/null

  # Safe by default: never drop or clean objects in the target database.
  # Restore into a newly created, isolated empty database. If objects already
  # exist, --single-transaction rolls back the attempted restore on failure.
  pg_restore \
    --exit-on-error \
    --single-transaction \
    --no-owner \
    --no-acl \
    --dbname="$POSTGRES_DB" \
    "$BACKUP_NAME"
)

printf '%s\n' "Restored $BACKUP_FILE into $POSTGRES_DB without dropping existing objects"
