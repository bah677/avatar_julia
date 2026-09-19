#!/usr/bin/env bash
# Применяет все SQL из db/sql/*.sql по имени к пустой PostgreSQL БД (создаёт схему таблиц).
# Требуются в окружении: DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME (или подгрузите .env).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

: "${DB_HOST:?Set DB_HOST}"
: "${DB_PORT:?Set DB_PORT}"
: "${DB_USER:?Set DB_USER}"
: "${DB_PASSWORD:?Set DB_PASSWORD}"
: "${DB_NAME:?Set DB_NAME}"

export PGPASSWORD="$DB_PASSWORD"
CONN="host=${DB_HOST} port=${DB_PORT} user=${DB_USER} dbname=${DB_NAME}"

mapfile -t SQL_FILES < <(find "$ROOT/db/sql" -maxdepth 1 -name '*.sql' -print | LC_ALL=C sort)

if [[ ${#SQL_FILES[@]} -eq 0 ]]; then
  echo "Нет файлов в $ROOT/db/sql" >&2
  exit 1
fi

for sql in "${SQL_FILES[@]}"; do
  echo "==> $(basename "$sql")"
  psql -v ON_ERROR_STOP=1 "$CONN" -f "$sql"
done

echo "Готово: применено ${#SQL_FILES[@]} файлов."
