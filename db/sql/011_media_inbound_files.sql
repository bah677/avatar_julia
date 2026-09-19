-- Архив входящих медиа на диск + учёт в БД (`MediaArchiveMixin`, `storage/db/media_archive.py`).

CREATE TABLE IF NOT EXISTS media_inbound_files (
    id BIGSERIAL PRIMARY KEY,

    user_id BIGINT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    chat_id BIGINT NOT NULL,
    telegram_message_id BIGINT NOT NULL,

    file_unique_id TEXT,
    file_id_at_capture TEXT NOT NULL,
    media_subtype TEXT NOT NULL,
    mime_type TEXT,
    file_size BIGINT,
    duration_sec INT,

    sha256_hex VARCHAR(64) NOT NULL,
    storage_relpath TEXT NOT NULL,

    messages_row_id BIGINT REFERENCES messages (id) ON DELETE SET NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_media_inbound_files_dedup UNIQUE (user_id, chat_id, telegram_message_id, sha256_hex)
);

CREATE INDEX IF NOT EXISTS idx_media_inbound_files_user_created
    ON media_inbound_files (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_media_inbound_files_messages_row
    ON media_inbound_files (messages_row_id)
    WHERE messages_row_id IS NOT NULL;
