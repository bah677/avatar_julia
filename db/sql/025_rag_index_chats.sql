-- Живые чаты, которые бот читает в RAG (кроме RAG_GROUPS из .env).

CREATE TABLE IF NOT EXISTS rag_index_chats (
    chat_id     BIGINT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    added_by    BIGINT,
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE rag_index_chats IS
    'Группы, куда бота добавили читать переписку: эксперт vs участники → RAG';
