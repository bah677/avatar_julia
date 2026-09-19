-- Таблица messages: MessageCopier, история LLM (get_private_chat_history), легаси add_message.
-- Проект biblia/club_ai — совместимая форма колонок.

CREATE TABLE IF NOT EXISTS messages (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,

    telegram_message_id BIGINT,
    chat_id BIGINT,  -- NULL в легаси add_message(); MessageCopier всегда задаёт chat_id
    chat_type TEXT,

    content TEXT,
    sender_type TEXT,
    role TEXT,
    message_type TEXT,
    subtype TEXT,

    raw_data JSONB,
    metadata JSONB,
    processing_time_ms INT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ,

    reply_to_message_id BIGINT,
    thread_id TEXT,

    edited_at TIMESTAMPTZ,
    is_edited BOOLEAN NOT NULL DEFAULT FALSE,
    version INT,

    -- add_message() / OpenAI Assistants (опционально)
    message_id TEXT,
    id_ass TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_user_created ON messages (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_user_chat ON messages (user_id, chat_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_private_history ON messages (user_id, created_at DESC)
    WHERE chat_type = 'private' AND deleted_at IS NULL;
