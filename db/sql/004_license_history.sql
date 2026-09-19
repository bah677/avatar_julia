-- storage/db/licenses.py — append_license_history

CREATE TABLE IF NOT EXISTS license_history (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    previous_expires_at TIMESTAMPTZ,
    new_expires_at TIMESTAMPTZ,
    source VARCHAR(128) NOT NULL,
    order_id BIGINT,
    payment_id BIGINT,
    referred_user_id BIGINT,
    meta JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_license_history_user ON license_history (user_id, created_at DESC);
