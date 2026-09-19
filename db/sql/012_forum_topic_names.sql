-- Кэш имён форум-топиков для RAG (см. ForumTopicNamesMixin, group_rag_indexer).

CREATE TABLE IF NOT EXISTS forum_topic_names (
    group_chat_id BIGINT NOT NULL,
    topic_id BIGINT NOT NULL,
    topic_name TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (group_chat_id, topic_id)
);

COMMENT ON TABLE forum_topic_names IS 'Telegram supergroup: id чата, id ветки (message_thread_id), название топика';
COMMENT ON COLUMN forum_topic_names.group_chat_id IS 'chat.id супергруппы';
COMMENT ON COLUMN forum_topic_names.topic_id IS 'message_thread_id топика';
COMMENT ON COLUMN forum_topic_names.topic_name IS 'Отображаемое имя (из forum_topic_created/edited или вручную)';

CREATE INDEX IF NOT EXISTS idx_forum_topic_names_group_updated
    ON forum_topic_names (group_chat_id, updated_at DESC);
