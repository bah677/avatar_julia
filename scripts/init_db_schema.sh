#!/usr/bin/env bash
#
# Инициализация схемы PostgreSQL на пустой базе (все db/sql/0NN_*.sql по порядку).
#
# Запуск от root (sudo): объекты создаёт суперпользователь postgres, затем владение
# передаётся роли приложения (DB_USER из .env), чтобы бот работал под этим пользователем.
#
# Требования:
#   - существуют БД и роль приложения (DB_NAME, DB_USER в .env);
#   - локально: при DB_HOST=localhost (или пусто) скрипт НЕ задаёт PGHOST — psql идёт через
#     Unix-сокет и обычно срабатывает peer для ОС-пользователя postgres (без пароля).
#     Если в .env указано 127.0.0.1 или другой хост — TCP; тогда в .env можно задать
#     INIT_POSTGRES_PASSWORD (пароль роли postgres в PostgreSQL) или использовать ~/.pgpass.
#
# Использование:
#   cd …/avatar && sudo ./scripts/init_db_schema.sh
#   (берётся avatar/.env рядом с репозиторием)
#
#   sudo ./scripts/init_db_schema.sh /home/appuser/dev/avatar/.env
#   (явный путь к .env, если не стандартный)
#
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Запустите под sudo: sudo $0" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SQL_DIR="${REPO_ROOT}/db/sql"
ENV_FILE="${1:-${REPO_ROOT}/.env}"

if [[ ! -d "$SQL_DIR" ]]; then
  echo "Не найден каталог SQL: $SQL_DIR" >&2
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Не найден .env: $ENV_FILE" >&2
  echo "Подсказка: запустите без аргумента из каталога avatar — тогда подхватится ${REPO_ROOT}/.env" >&2
  echo "  sudo ./scripts/init_db_schema.sh" >&2
  exit 1
fi

# Подтягиваем только нужные ключи (без полного source .env — меньше сюрпризов с пробелами/кавычками).
trim() { echo -n "$1" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'; }

DB_NAME=""
DB_USER=""
DB_HOST=""
DB_PORT="5432"
INIT_POSTGRES_PASSWORD=""

while IFS= read -r raw || [[ -n "$raw" ]]; do
  line="$(trim "$raw")"
  [[ -z "$line" || "$line" == \#* ]] && continue
  [[ "$line" != *=* ]] && continue
  key="$(trim "${line%%=*}")"
  val="$(trim "${line#*=}")"
  # срезать возможные кавычки по краям
  val="${val#\"}"
  val="${val%\"}"
  val="${val#\'}"
  val="${val%\'}"
  case "$key" in
    DB_NAME) DB_NAME="$val" ;;
    DB_USER) DB_USER="$val" ;;
    DB_HOST) DB_HOST="$val" ;;
    DB_PORT) DB_PORT="${val:-5432}" ;;
    INIT_POSTGRES_PASSWORD) INIT_POSTGRES_PASSWORD="$val" ;;
  esac
done < "$ENV_FILE"

# localhost / пусто → Unix-сокет (без TCP, без пароля для peer). 127.0.0.1 → TCP.
USE_UNIX_SOCKET=0
if [[ -z "${DB_HOST// }" ]]; then
  USE_UNIX_SOCKET=1
elif [[ "${DB_HOST,,}" == "localhost" ]]; then
  USE_UNIX_SOCKET=1
fi

if [[ -z "$DB_NAME" || -z "$DB_USER" ]]; then
  echo "В $ENV_FILE должны быть заданы DB_NAME и DB_USER." >&2
  exit 1
fi

if [[ ! "$DB_USER" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
  echo "DB_USER должен быть допустимым идентификатором PostgreSQL (буквы, цифры, _)." >&2
  exit 1
fi

mapfile -t SQL_FILES < <(find "$SQL_DIR" -maxdepth 1 -type f -name '[0-9][0-9][0-9]_*.sql' | LC_ALL=C sort)
if [[ "${#SQL_FILES[@]}" -eq 0 ]]; then
  echo "В $SQL_DIR нет файлов 0NN_*.sql" >&2
  exit 1
fi

if [[ "$USE_UNIX_SOCKET" -eq 1 ]]; then
  echo "==> База: $DB_NAME, приложение: $DB_USER, подключение: Unix-сокет (роль postgres без PGHOST)"
else
  echo "==> База: $DB_NAME, приложение: $DB_USER, TCP: $DB_HOST:$DB_PORT"
fi
echo "==> SQL-файлы: ${#SQL_FILES[@]} шт."

run_psql() {
  # Все запросы от имени ОС-пользователя postgres (роль в PostgreSQL).
  if [[ "$USE_UNIX_SOCKET" -eq 1 ]]; then
    sudo -u postgres -- psql -v ON_ERROR_STOP=1 "$@"
  else
    if [[ -n "$INIT_POSTGRES_PASSWORD" ]]; then
      sudo -u postgres -- env \
        PGPASSWORD="$INIT_POSTGRES_PASSWORD" \
        PGHOST="$DB_HOST" \
        PGPORT="$DB_PORT" \
        psql -v ON_ERROR_STOP=1 "$@"
    else
      sudo -u postgres -- env PGHOST="$DB_HOST" PGPORT="$DB_PORT" psql -v ON_ERROR_STOP=1 "$@"
    fi
  fi
}

for f in "${SQL_FILES[@]}"; do
  echo "==> $(basename "$f")"
  run_psql -d "$DB_NAME" -f "$f"
done

echo "==> Передача владения: public.*, владелец postgres → $DB_USER (без REASSIGN OWNED — он ломается на системных объектах)"
run_psql -d "$DB_NAME" -v ON_ERROR_STOP=1 <<SQL
DO \$body\$
DECLARE
  r RECORD;
  owner_oid oid := (SELECT oid FROM pg_roles WHERE rolname = 'postgres');
  app text := '${DB_USER}';
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = app) THEN
    RAISE EXCEPTION 'роль % не существует', app;
  END IF;

  FOR r IN
    SELECT c.relkind, n.nspname AS sch, c.relname AS rel
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relowner = owner_oid
      AND c.relkind IN ('r', 'p')
  LOOP
    EXECUTE format('ALTER TABLE %I.%I OWNER TO %I', r.sch, r.rel, app);
  END LOOP;

  FOR r IN
    SELECT n.nspname AS sch, c.relname AS rel
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relowner = owner_oid
      AND c.relkind = 'S'
  LOOP
    EXECUTE format('ALTER SEQUENCE %I.%I OWNER TO %I', r.sch, r.rel, app);
  END LOOP;

  FOR r IN
    SELECT n.nspname AS sch, c.relname AS rel, c.relkind
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relowner = owner_oid
      AND c.relkind = 'v'
  LOOP
    EXECUTE format('ALTER VIEW %I.%I OWNER TO %I', r.sch, r.rel, app);
  END LOOP;

  FOR r IN
    SELECT n.nspname AS sch, c.relname AS rel
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relowner = owner_oid
      AND c.relkind = 'm'
  LOOP
    EXECUTE format('ALTER MATERIALIZED VIEW %I.%I OWNER TO %I', r.sch, r.rel, app);
  END LOOP;

  FOR r IN
    SELECT n.nspname AS sch, p.proname, pg_get_function_identity_arguments(p.oid) AS args
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public'
      AND p.proowner = owner_oid
      AND p.prokind = 'f'
  LOOP
    EXECUTE format(
      'ALTER FUNCTION %I.%I(%s) OWNER TO %I',
      r.sch, r.proname, r.args, app
    );
  END LOOP;
END;
\$body\$;

GRANT CONNECT ON DATABASE "${DB_NAME}" TO "${DB_USER}";
GRANT USAGE, CREATE ON SCHEMA public TO "${DB_USER}";
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO "${DB_USER}";
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO "${DB_USER}";
GRANT ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public TO "${DB_USER}";

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON TABLES TO "${DB_USER}";
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON SEQUENCES TO "${DB_USER}";
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON FUNCTIONS TO "${DB_USER}";
SQL

echo "==> Готово. Проверка списка таблиц:"
run_psql -d "$DB_NAME" -c '\dt public.*'

echo
echo "Дальше (при необходимости) выдайте лицензию вручную, см. db/sql/README.md."
