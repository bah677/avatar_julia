-- Журнал interaction_logs (storage/db/messages.py log_interaction, InboundLoggingMiddleware).

CREATE TABLE IF NOT EXISTS interaction_logs (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    event_category TEXT NOT NULL,
    event_type TEXT NOT NULL,
    processing_time_ms INT,
    message_id BIGINT,
    data JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    update_id BIGINT,
    chat_id BIGINT,
    chat_type TEXT,
    telegram_message_id BIGINT,
    callback_data TEXT,
    command TEXT,
    source TEXT,
    outcome TEXT
);

CREATE INDEX IF NOT EXISTS idx_interaction_logs_user_created
    ON interaction_logs (user_id, created_at DESC);
