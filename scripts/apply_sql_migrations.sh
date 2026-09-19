#!/usr/bin/env bash
set -euo pipefail
#
# Накат SQL из db/sql/0NN_*.sql. Уже применённые файлы — в schema_migrations.
#
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f ".env" ]]; then
  echo "ERROR: нет .env в $ROOT_DIR" >&2
  exit 1
fi

trim() { echo -n "$1" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'; }

DB_NAME=""
DB_USER=""
DB_PASSWORD=""
DB_HOST="localhost"
DB_PORT="5432"

while IFS= read -r raw || [[ -n "$raw" ]]; do
  line="$(trim "$raw")"
  [[ -z "$line" || "$line" == \#* ]] && continue
  [[ "$line" != *=* ]] && continue
  key="$(trim "${line%%=*}")"
  val="$(trim "${line#*=}")"
  val="${val#\"}"; val="${val%\"}"
  val="${val#\'}"; val="${val%\'}"
  case "$key" in
    DB_NAME) DB_NAME="$val" ;;
    BIBLIA_DB_NAME) [[ -z "$DB_NAME" ]] && DB_NAME="$val" ;;
    DB_USER) DB_USER="$val" ;;
    DB_PASSWORD) DB_PASSWORD="$val" ;;
    DB_HOST) DB_HOST="${val:-localhost}" ;;
    DB_PORT) DB_PORT="${val:-5432}" ;;
  esac
done < ".env"

if [[ -z "$DB_NAME" || -z "$DB_USER" || -z "$DB_PASSWORD" ]]; then
  echo "ERROR: в .env нужны DB_NAME, DB_USER, DB_PASSWORD" >&2
  exit 1
fi

command -v psql >/dev/null || { echo "ERROR: нужен psql" >&2; exit 1; }

echo "==> миграции ${DB_USER}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
export PGPASSWORD="$DB_PASSWORD"

psql_exec() {
  psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 "$@"
}

psql_exec -c "
CREATE TABLE IF NOT EXISTS public.schema_migrations (
  filename TEXT PRIMARY KEY,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);"

shopt -s nullglob
mapfile -t migration_files < <(find db/sql -maxdepth 1 -type f -name '[0-9][0-9][0-9]_*.sql' | LC_ALL=C sort)
shopt -u nullglob

if [[ "${#migration_files[@]}" -eq 0 ]]; then
  echo "ERROR: нет db/sql/0NN_*.sql" >&2
  exit 1
fi

applied=0
skipped=0
for file in "${migration_files[@]}"; do
  rel="${file#./}"
  found="$(psql_exec -tA -c "SELECT 1 FROM public.schema_migrations WHERE filename = '${rel}' LIMIT 1;")"
  if [[ "$found" == "1" ]]; then
    echo "   skip  ${rel}"
    skipped=$((skipped + 1))
    continue
  fi
  echo "   apply ${rel}"
  psql_exec -f "$file"
  psql_exec -c "INSERT INTO public.schema_migrations(filename) VALUES ('${rel}');"
  applied=$((applied + 1))
done

unset PGPASSWORD
echo "==> готово: применено ${applied}, пропущено ${skipped}"
