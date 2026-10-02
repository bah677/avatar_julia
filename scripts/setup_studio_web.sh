#!/usr/bin/env bash
# Поддомен для веб-студии: nginx + HTTPS (Let's Encrypt).
#
# Что делает:
#   1) проверяет, что DNS уже указывает на этот сервер;
#   2) ставит конфиг nginx из deploy/nginx/studio.http.conf;
#   3) перечитывает nginx;
#   4) выпускает сертификат через certbot --nginx (он же добавит редирект на HTTPS).
#
# Запуск (нужен sudo):
#   sudo ./scripts/setup_studio_web.sh                    # домен из .env (WEB_DOMAIN)
#   sudo ./scripts/setup_studio_web.sh studio.mironbot.ru # домен аргументом
#
# Повторный запуск безопасен: конфиг перезаписывается, сертификат продлевается.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SITE_NAME="avatar-studio"
TEMPLATE="deploy/nginx/studio.http.conf"
SITE_AVAILABLE="/etc/nginx/sites-available/${SITE_NAME}"
SITE_ENABLED="/etc/nginx/sites-enabled/${SITE_NAME}"

env_value() {
  local key="$1"
  [[ -f .env ]] || return 0
  sed -n "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*//p" .env \
    | tail -1 | tr -d '"'"'" | tr -d '\r' | sed -e 's/[[:space:]]*$//'
}

DOMAIN="${1:-$(env_value WEB_DOMAIN)}"
PORT="$(env_value WEB_PORT)"
PORT="${PORT:-8800}"

if [[ -z "$DOMAIN" ]]; then
  echo "ERROR: не задан домен. Укажите аргументом или WEB_DOMAIN в .env" >&2
  exit 1
fi
if [[ ! -f "$TEMPLATE" ]]; then
  echo "ERROR: нет шаблона $TEMPLATE" >&2
  exit 1
fi
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Запустите под sudo: sudo $0 ${DOMAIN}" >&2
  exit 1
fi

# Системный резолвер кэширует «домена нет» до 3 часов (negative TTL в SOA),
# поэтому спрашиваем авторитативные серверы зоны напрямую.
resolve_ip() {
  local host="$1" zone ns ip
  if command -v dig >/dev/null 2>&1; then
    zone="${host#*.}"
    for ns in $(dig +short "$zone" NS 2>/dev/null | head -2); do
      ip="$(dig +short "@${ns}" "$host" A 2>/dev/null | grep -E '^[0-9.]+$' | head -1)"
      if [[ -n "$ip" ]]; then
        echo "$ip"
        return 0
      fi
    done
    ip="$(dig +short "$host" A 2>/dev/null | grep -E '^[0-9.]+$' | head -1)"
    if [[ -n "$ip" ]]; then
      echo "$ip"
      return 0
    fi
  fi
  getent ahostsv4 "$host" 2>/dev/null | awk '{print $1; exit}'
}

# Свежая запись могла не дойти до локального кэша — сбрасываем, раз уже под root.
if command -v resolvectl >/dev/null 2>&1; then
  resolvectl flush-caches >/dev/null 2>&1 || true
fi

SERVER_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.*src \([0-9.]*\).*/\1/p' | head -1)"
DOMAIN_IP="$(resolve_ip "$DOMAIN")"

echo "==> домен:      ${DOMAIN}"
echo "==> этот сервер: ${SERVER_IP:-неизвестно}"
echo "==> DNS отдаёт:  ${DOMAIN_IP:-ничего} (по данным серверов зоны)"

if [[ -z "$DOMAIN_IP" ]]; then
  cat >&2 <<DNS
ERROR: домен ${DOMAIN} пока не разрешается.

Пропишите у регистратора:
  Тип:      A
  Имя:      ${DOMAIN%%.*}
  Значение: ${SERVER_IP:-<ip этого сервера>}
  TTL:      300

Подождите несколько минут и запустите скрипт снова.
DNS
  exit 1
fi

if [[ -n "$SERVER_IP" && "$DOMAIN_IP" != "$SERVER_IP" ]]; then
  echo "ERROR: ${DOMAIN} указывает на ${DOMAIN_IP}, а сервер ${SERVER_IP}." >&2
  echo "Поправьте A-запись и повторите." >&2
  exit 1
fi

echo "==> [1/4] конфиг nginx → ${SITE_AVAILABLE}"
sed -e "s/DOMAIN/${DOMAIN}/g" -e "s/UPSTREAM_PORT/${PORT}/g" "$TEMPLATE" > "$SITE_AVAILABLE"
ln -sfn "$SITE_AVAILABLE" "$SITE_ENABLED"

echo "==> [2/4] проверка конфигурации"
nginx -t

echo "==> [3/4] перезагрузка nginx"
systemctl reload nginx

echo "==> [4/4] сертификат Let's Encrypt"
if [[ -d "/etc/letsencrypt/live/${DOMAIN}" ]]; then
  echo "    сертификат уже есть, обновляю конфигурацию"
  certbot --nginx -d "$DOMAIN" --reinstall --redirect --non-interactive || true
else
  EMAIL="$(env_value CERTBOT_EMAIL)"
  if [[ -n "$EMAIL" ]]; then
    certbot --nginx -d "$DOMAIN" --agree-tos -m "$EMAIL" --redirect --non-interactive
  else
    # Без CERTBOT_EMAIL certbot спросит почту сам.
    certbot --nginx -d "$DOMAIN" --redirect
  fi
fi

systemctl reload nginx

cat <<DONE

Готово: https://${DOMAIN}

Проверка:
  curl -sS https://${DOMAIN}/healthz

Если /healthz не отвечает — студия в боте не поднята. Проверьте в .env
WEB_ENABLED=1 и WEB_SECRET, затем перезапустите бота:
  sudo supervisorctl restart avatar:avatar_julia
DONE
