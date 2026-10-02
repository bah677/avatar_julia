-- Веб-студия: чаты с выбранным контекстом и их сообщения.

CREATE TABLE IF NOT EXISTS web_chats (
    id          UUID PRIMARY KEY,
    product_id  TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    format      TEXT NOT NULL DEFAULT 'stories',
    stage       TEXT NOT NULL DEFAULT 'warmup',
    focus       TEXT NOT NULL DEFAULT '',
    context     JSONB NOT NULL DEFAULT '{}'::jsonb,
    archived    BOOLEAN NOT NULL DEFAULT FALSE,
    created_by  BIGINT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_web_chats_recent
    ON web_chats (product_id, archived, updated_at DESC);

COMMENT ON TABLE web_chats IS
    'Веб-студия: один чат = один набор объектов контекста + формат, этап, фокус';
COMMENT ON COLUMN web_chats.context IS
    '{"source_ids":[...],"lesson_keys":[...],"passports":["expert","product","launch"],"live":["testimonial","dialog"]}';

CREATE TABLE IF NOT EXISTS web_chat_messages (
    id                BIGSERIAL PRIMARY KEY,
    chat_id           UUID NOT NULL REFERENCES web_chats (id) ON DELETE CASCADE,
    role              TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    text              TEXT NOT NULL,
    model             TEXT NOT NULL DEFAULT '',
    prompt_tokens     INT NOT NULL DEFAULT 0,
    completion_tokens INT NOT NULL DEFAULT 0,
    meta              JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_web_chat_messages_chat
    ON web_chat_messages (chat_id, id);
