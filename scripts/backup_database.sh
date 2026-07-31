#!/usr/bin/env bash
set -euo pipefail

DB_PATH="${DND_DB_PATH:-$(dirname "$0")/../data/characters.db}"
BACKUP_DIR="${DND_BACKUP_DIR:-$(dirname "$0")/../data/backups}"
mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d%H%M%S)"
DEST="$BACKUP_DIR/characters.db.$STAMP"

if [[ ! -f "$DB_PATH" ]]; then
  printf 'Database not found: %s\n' "$DB_PATH" >&2
  exit 1
fi

sqlite3 "$DB_PATH" ".backup '$DEST'"
sqlite3 "$DEST" "PRAGMA integrity_check;" | grep -qx 'ok'
printf '%s\n' "$DEST"
