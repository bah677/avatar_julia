#!/usr/bin/env bash
#
# Накат julia_avatar (процесс из /home/appuser/dev/avatar, supervisor avatar:avatar_julia).
#
#   1) tar-снимок кода (без .venv, data, log, chroma_data, .env)
#   2) pg_dump БД
#   3) ротация бэкапов 7д
#   4) SQL-миграции db/sql/0NN_*.sql (schema_migrations)
#   5) supervisorctl restart avatar:avatar_julia
#   6) страница обновлений https://updates.mironbot.ru/julia_avatar
#   7) git commit + push
#
#   ./scripts/deploy_prod.sh
#   SKIP_GIT_PUSH=1 ./scripts/deploy_prod.sh
#   SKIP_RESTART=1 ./scripts/deploy_prod.sh
#   RUN_MIGRATIONS=0 ./scripts/deploy_prod.sh
#
set -euo pipefail

if [[ "${SKIP_SUDO_REEXEC:-0}" != "1" && "$(id -u)" -ne 0 ]]; then
  echo "==> Нужны права root — перезапуск через sudo..."
  exec sudo -E "$(command -v bash)" "${BASH_SOURCE[0]}" "$@"
fi

AVATAR_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KOSTYA_ROOT="${KOSTYA_ROOT:-/home/appuser/dev/kostya}"
SUPERVISOR_NAME="${SUPERVISOR_NAME:-avatar:avatar_julia}"
RUN_USER="${DEPLOY_RUN_USER:-${SUDO_USER:-appuser}}"
APP_USER="${APP_USER:-appuser}"

SKIP_GIT_PUSH="${SKIP_GIT_PUSH:-0}"
SKIP_RETENTION="${SKIP_RETENTION:-0}"
SKIP_RESTART="${SKIP_RESTART:-0}"
SKIP_TAR_BACKUP="${SKIP_TAR_BACKUP:-0}"
SKIP_PGDUMP="${SKIP_PGDUMP:-0}"
RUN_MIGRATIONS="${RUN_MIGRATIONS:-1}"
SKIP_ECOSYSTEM_UPDATES="${SKIP_ECOSYSTEM_UPDATES:-0}"

CODE_SNAPS="${AVATAR_CODE_SNAPSHOTS_DIR:-/home/appuser/backups/avatar_julia/code}"
DB_DUMPS="${AVATAR_DB_DUMPS_DIR:-/home/appuser/backups/avatar_julia/db}"
GIT_REMOTE_URL="${GIT_REMOTE_URL:-git@github.com:bah677/julia_avatar.git}"
GIT_BRANCH="${GIT_BRANCH:-main}"
RETENTION_SH="${KOSTYA_ROOT}/scripts/disk_retention.sh"
UPDATES_ROOT="${KOSTYA_ROOT}/ecosystem_updates"
TS="$(date +%Y%m%d_%H%M%S)"

die() { echo "ERROR: $*" >&2; exit 1; }

[[ -d "${AVATAR_ROOT}" ]] || die "нет ${AVATAR_ROOT}"
[[ -f "${AVATAR_ROOT}/main.py" ]] || die "нет ${AVATAR_ROOT}/main.py"
[[ -f "${AVATAR_ROOT}/.env" ]] || die "нет ${AVATAR_ROOT}/.env"

echo "julia_avatar deploy"
echo "  root: ${AVATAR_ROOT}"
echo "  supervisor: ${SUPERVISOR_NAME}"
echo "  ts: ${TS}"

read_env() {
  local key="$1"
  local val=""
  val="$(grep -E "^${key}=" "${AVATAR_ROOT}/.env" | tail -n1 | cut -d= -f2- || true)"
  val="${val#\"}"; val="${val%\"}"
  val="${val#\'}"; val="${val%\'}"
  echo -n "$val"
}

DB_NAME="$(read_env DB_NAME)"
[[ -n "$DB_NAME" ]] || DB_NAME="$(read_env BIBLIA_DB_NAME)"
[[ -n "$DB_NAME" ]] || die "в .env нет DB_NAME"

# ---------- 1. tar кода ----------
if [[ "${SKIP_TAR_BACKUP}" != "1" ]]; then
  mkdir -p "${CODE_SNAPS}"
  archive="${CODE_SNAPS}/julia_avatar_code_${TS}.tgz"
  echo "==> [1/7] архив кода: ${archive}"
  tar -czf "${archive}" \
    --exclude='./.venv' \
    --exclude='./venv' \
    --exclude='./data' \
    --exclude='./log' \
    --exclude='./chroma_data' \
    --exclude='./.env' \
    --exclude='./.env.*' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    -C "${AVATAR_ROOT}" .
  chown "${RUN_USER}:${RUN_USER}" "${archive}" 2>/dev/null || true
  echo "    $(du -h "${archive}" | cut -f1)"
else
  echo "==> [1/7] SKIP_TAR_BACKUP=1"
fi

# ---------- 2. pg_dump ----------
if [[ "${SKIP_PGDUMP}" != "1" ]]; then
  mkdir -p "${DB_DUMPS}"
  chown "${APP_USER}:${APP_USER}" "${DB_DUMPS}" 2>/dev/null || true
  dump_file="${DB_DUMPS}/julia_avatar_db_${TS}.dump"
  echo "==> [2/7] pg_dump ${DB_NAME} → ${dump_file}"
  sudo -u postgres pg_dump --format=custom --no-owner "$DB_NAME" > "$dump_file"
  chmod 644 "$dump_file"
  chown "${RUN_USER}:${RUN_USER}" "$dump_file" 2>/dev/null || true
  echo "    $(du -h "$dump_file" | cut -f1)"
else
  echo "==> [2/7] SKIP_PGDUMP=1"
fi

# ---------- 3. retention ----------
if [[ "${SKIP_RETENTION}" != "1" && -x "${RETENTION_SH}" ]]; then
  echo "==> [3/7] disk retention (backups 7d)"
  BACKUP_DAYS=7 DATA_DAYS=7 LOG_ARC_DAYS=30 \
    "${RETENTION_SH}" deploy --apply || true
  find "${CODE_SNAPS}" -type f -name 'julia_avatar_code_*.tgz' -mtime +7 -delete 2>/dev/null || true
  find "${DB_DUMPS}" -type f -name 'julia_avatar_db_*.dump' -mtime +7 -delete 2>/dev/null || true
else
  echo "==> [3/7] retention пропущен"
fi

# ---------- 4. миграции ----------
if [[ "${RUN_MIGRATIONS}" == "1" ]]; then
  echo "==> [4/7] SQL-миграции"
  if [[ "$(id -u)" -eq 0 ]]; then
    sudo -u "${RUN_USER}" -- bash "${AVATAR_ROOT}/scripts/apply_sql_migrations.sh"
  else
    bash "${AVATAR_ROOT}/scripts/apply_sql_migrations.sh"
  fi
else
  echo "==> [4/7] RUN_MIGRATIONS=0"
fi

# ---------- 5. supervisor ----------
if [[ "${SKIP_RESTART}" != "1" ]]; then
  echo "==> [5/7] supervisorctl restart ${SUPERVISOR_NAME}"
  supervisorctl restart "${SUPERVISOR_NAME}"
  supervisorctl status "${SUPERVISOR_NAME}" || true
else
  echo "==> [5/7] SKIP_RESTART=1"
fi

# ---------- 6. updates.mironbot.ru/julia_avatar ----------
if [[ "${SKIP_ECOSYSTEM_UPDATES}" != "1" && -d "${UPDATES_ROOT}" ]]; then
  echo "==> [6/7] updates.mironbot.ru/julia_avatar"
  today_msk="$(TZ=Europe/Moscow date +%F)"
  src_inbox="${AVATAR_ROOT}/updates/inbox/${today_msk}.md"
  dest_dir="${UPDATES_ROOT}/projects/julia_avatar/inbox"
  mkdir -p "${dest_dir}" "${UPDATES_ROOT}/projects/julia_avatar/notes"
  if [[ -f "${src_inbox}" ]]; then
    cp -f "${src_inbox}" "${dest_dir}/${today_msk}.md"
    chown -R "${RUN_USER}:${RUN_USER}" "${UPDATES_ROOT}/projects/julia_avatar" 2>/dev/null || true
  fi
  if [[ "$(id -u)" -eq 0 ]]; then
    sudo -u "${RUN_USER}" bash "${UPDATES_ROOT}/scripts/publish.sh" || \
      echo "WARNING: publish ecosystem_updates failed"
  else
    bash "${UPDATES_ROOT}/scripts/publish.sh" || \
      echo "WARNING: publish ecosystem_updates failed"
  fi
else
  echo "==> [6/7] обновления пропущены"
fi

# ---------- 7. git push ----------
if [[ "${SKIP_GIT_PUSH}" != "1" ]]; then
  echo "==> [7/7] git push GitHub"
  if [[ "$(id -u)" -eq 0 ]]; then
    sudo -u "${RUN_USER}" env \
      AVATAR_ROOT="${AVATAR_ROOT}" \
      GIT_REMOTE_URL="${GIT_REMOTE_URL}" \
      GIT_BRANCH="${GIT_BRANCH}" \
      bash "${AVATAR_ROOT}/scripts/git_push_deploy.sh"
  else
    env AVATAR_ROOT="${AVATAR_ROOT}" GIT_REMOTE_URL="${GIT_REMOTE_URL}" GIT_BRANCH="${GIT_BRANCH}" \
      bash "${AVATAR_ROOT}/scripts/git_push_deploy.sh"
  fi
else
  echo "==> [7/7] SKIP_GIT_PUSH=1"
fi

echo "Готово."
echo "  страница: https://updates.mironbot.ru/julia_avatar"
