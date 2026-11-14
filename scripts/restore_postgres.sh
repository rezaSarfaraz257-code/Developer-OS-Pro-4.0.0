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

  # Enforce the documented empty-database precondition before pg_restore.
  # This prevents silently merging a backup into a database that contains
  # unrelated tables, routines, types, or custom schemas (even without name
  # collisions). The default public schema is allowed only while it is empty.
  target_has_objects="$(psql --no-psqlrc --set=ON_ERROR_STOP=1 --tuples-only --no-align \
    --dbname="$POSTGRES_DB" \
    --command="SELECT (
      EXISTS (
        SELECT 1 FROM pg_namespace
        WHERE nspname NOT IN ('pg_catalog', 'information_schema', 'public')
          AND nspname NOT LIKE 'pg_toast%'
          AND nspname NOT LIKE 'pg_temp_%'
      )
      OR EXISTS (
        SELECT 1 FROM pg_class AS c
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
          AND n.nspname NOT LIKE 'pg_toast%'
          AND n.nspname NOT LIKE 'pg_temp_%'
      )
      OR EXISTS (
        SELECT 1 FROM pg_proc AS p
        JOIN pg_namespace AS n ON n.oid = p.pronamespace
        WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
          AND n.nspname NOT LIKE 'pg_toast%'
          AND n.nspname NOT LIKE 'pg_temp_%'
      )
      OR EXISTS (
        SELECT 1 FROM pg_type AS t
        JOIN pg_namespace AS n ON n.oid = t.typnamespace
        WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
          AND n.nspname NOT LIKE 'pg_toast%'
          AND n.nspname NOT LIKE 'pg_temp_%'
          AND t.typtype IN ('b', 'e', 'r', 'm', 'd')
          AND t.typelem = 0
          AND t.typrelid = 0
      )
    )::text")"
  if [ "$target_has_objects" != "f" ]; then
    echo "Refusing to restore into a non-empty database: $POSTGRES_DB" >&2
    exit 1
  fi

  # Safe by default: never drop or clean objects in the target database.
  # The preflight above enforces isolation; this transaction remains a second
  # line of defense against conflicts or failures during the restore itself.
  pg_restore \
    --exit-on-error \
    --single-transaction \
    --no-owner \
    --no-acl \
    --dbname="$POSTGRES_DB" \
    "$BACKUP_NAME"
)

printf '%s\n' "Restored $BACKUP_FILE into $POSTGRES_DB without dropping existing objects"
