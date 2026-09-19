#!/usr/bin/env bash
# Коммит и push репозитория julia_avatar в GitHub.
set -euo pipefail

AVATAR_ROOT="${AVATAR_ROOT:-/home/appuser/dev/avatar}"
GIT_REMOTE_URL="${GIT_REMOTE_URL:-git@github.com:bah677/julia_avatar.git}"
GIT_BRANCH="${GIT_BRANCH:-main}"
GIT_AUTHOR_NAME="${GIT_AUTHOR_NAME:-bah677}"
GIT_AUTHOR_EMAIL="${GIT_AUTHOR_EMAIL:-bah677@users.noreply.github.com}"
GIT_COMMITTER_NAME="${GIT_COMMITTER_NAME:-$GIT_AUTHOR_NAME}"
GIT_COMMITTER_EMAIL="${GIT_COMMITTER_EMAIL:-$GIT_AUTHOR_EMAIL}"

die() { echo "ERROR [git_push]: $*" >&2; exit 1; }

[[ -d "${AVATAR_ROOT}" ]] || die "Нет каталога ${AVATAR_ROOT}"
cd "${AVATAR_ROOT}"
command -v git >/dev/null || die "нужен git"

if [[ -f .env ]] && [[ -d .git ]]; then
  git check-ignore -q .env || die ".env не в .gitignore — пуш отменён"
fi

if [[ ! -d .git ]]; then
  git init -b "${GIT_BRANCH}"
fi

git remote get-url origin &>/dev/null || git remote add origin "${GIT_REMOTE_URL}"
git remote set-url origin "${GIT_REMOTE_URL}"

git add -A
if git diff --cached --quiet; then
  echo "==> [git] Нет изменений для коммита — только push"
else
  msg="deploy $(date +%Y-%m-%d_%H:%M:%S)"
  GIT_AUTHOR_NAME="${GIT_AUTHOR_NAME}" GIT_AUTHOR_EMAIL="${GIT_AUTHOR_EMAIL}" \
  GIT_COMMITTER_NAME="${GIT_COMMITTER_NAME}" GIT_COMMITTER_EMAIL="${GIT_COMMITTER_EMAIL}" \
    git commit -m "${msg}"
  echo "==> [git] Коммит: ${msg}"
fi

echo "==> [git] fetch + rebase origin/${GIT_BRANCH}"
git fetch origin "${GIT_BRANCH}" || true
if git rev-parse --verify "origin/${GIT_BRANCH}" >/dev/null 2>&1; then
  if ! git merge-base --is-ancestor "origin/${GIT_BRANCH}" HEAD 2>/dev/null; then
    GIT_AUTHOR_NAME="${GIT_AUTHOR_NAME}" GIT_AUTHOR_EMAIL="${GIT_AUTHOR_EMAIL}" \
    GIT_COMMITTER_NAME="${GIT_COMMITTER_NAME}" GIT_COMMITTER_EMAIL="${GIT_COMMITTER_EMAIL}" \
      git rebase "origin/${GIT_BRANCH}" || die "rebase не удался"
  fi
else
  echo "==> [git] remote-ветка origin/${GIT_BRANCH} пока нет — push создаст её"
fi

echo "==> [git] push origin ${GIT_BRANCH}"
git push -u origin "${GIT_BRANCH}"
echo "==> [git] OK: ${GIT_REMOTE_URL} (${GIT_BRANCH})"
