#!/usr/bin/env sh
set -eu
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
: "${BACKUP_FILE:?BACKUP_FILE must point to a pg_dump custom-format file}"
if [ ! -f "$BACKUP_FILE" ]; then echo "Backup file not found: $BACKUP_FILE" >&2; exit 1; fi
export PGPASSWORD="$POSTGRES_PASSWORD"
pg_restore --clean --if-exists --no-owner --no-acl --dbname="$POSTGRES_DB" "$BACKUP_FILE"
unset PGPASSWORD
echo "Restored $BACKUP_FILE into $POSTGRES_DB"
