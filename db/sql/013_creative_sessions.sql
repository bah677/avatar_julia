-- Сессия «задачи» для RAG-диалога (/new): выбор типа/продукта, тема, ходы в рамках task_id.

CREATE TABLE IF NOT EXISTS creative_sessions (
    user_id BIGINT PRIMARY KEY REFERENCES users (user_id) ON DELETE CASCADE,
    state TEXT NOT NULL DEFAULT 'idle',
    product TEXT,
    content_type TEXT,
    topic TEXT,
    task_id UUID,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE creative_sessions IS 'Личка: машина состояний для /new — продукт, тип контента, тема, активная задача';
COMMENT ON COLUMN creative_sessions.state IS 'idle | confirm_new | pick_content_type | pick_product | awaiting_custom_content_type | awaiting_custom_product | awaiting_topic | active';

CREATE TABLE IF NOT EXISTS creative_task_turns (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    task_id UUID NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_creative_task_turns_task_time
    ON creative_task_turns (task_id, id);

COMMENT ON TABLE creative_task_turns IS 'Сообщения внутри одной задачи (task_id) для подстановки в LLM без общей истории ЛС';
