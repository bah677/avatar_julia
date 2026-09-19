-- Таблица users (storage/db/users.py, bot/logging/message_copier FK)

CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    username VARCHAR(255),
    first_name VARCHAR(255),
    last_name VARCHAR(255),
    language_code VARCHAR(32),
    is_premium BOOLEAN NOT NULL DEFAULT FALSE,
    last_activity TIMESTAMPTZ,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    onboarding_complete BOOLEAN NOT NULL DEFAULT FALSE,
    openai_thread_id VARCHAR(255),
    questions_asked INT NOT NULL DEFAULT 0,

    bd DATE,
    profile TEXT,
    first_answer_rating VARCHAR(64),
    rated_at TIMESTAMPTZ,
    timezone_offset INT,

    is_banned BOOLEAN NOT NULL DEFAULT FALSE,

    agent_session_id VARCHAR(512),

    show_donation_on_next_response BOOLEAN NOT NULL DEFAULT FALSE,
    donation_button INT
);

CREATE INDEX IF NOT EXISTS idx_users_last_activity ON users (last_activity DESC);
CREATE INDEX IF NOT EXISTS idx_users_is_banned ON users (is_banned) WHERE is_banned = TRUE;
