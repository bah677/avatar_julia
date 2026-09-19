-- Лицензии: доступ по белому списку (get_user_active_license, user_has_active_license).
-- payment_id пока без FK — таблица payments появится в отдельном скрипте при подключении оплат.

CREATE TABLE IF NOT EXISTS license (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    license_type VARCHAR(64) NOT NULL DEFAULT 'subscription',
    expires_at TIMESTAMPTZ NOT NULL,
    payment_id BIGINT,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_license_user UNIQUE (user_id)
);

CREATE INDEX IF NOT EXISTS idx_license_active_expires ON license (status, expires_at);
CREATE INDEX IF NOT EXISTS idx_license_user_status ON license (user_id, status);
