-- Вход в веб-студию через Telegram: одноразовые коды и сессии браузера.

CREATE TABLE IF NOT EXISTS web_login_codes (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL,
    code_hash   TEXT NOT NULL,
    attempts    INT NOT NULL DEFAULT 0,
    used_at     TIMESTAMPTZ,
    expires_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_web_login_codes_user
    ON web_login_codes (user_id, created_at DESC);

COMMENT ON TABLE web_login_codes IS
    'Код из Telegram для входа в студию: храним только хеш, живёт минуты';

CREATE TABLE IF NOT EXISTS web_sessions (
    token_hash   TEXT PRIMARY KEY,
    user_id      BIGINT NOT NULL,
    user_agent   TEXT NOT NULL DEFAULT '',
    ip           TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at   TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_web_sessions_user
    ON web_sessions (user_id, last_seen_at DESC);

COMMENT ON TABLE web_sessions IS
    'Сессии браузера веб-студии: кто вошёл, на что писать расход токенов';
