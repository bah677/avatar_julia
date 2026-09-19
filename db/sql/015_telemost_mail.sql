-- Письма Телемоста: ожидание решения админа и учёт импорта в RAG.

CREATE TABLE IF NOT EXISTS telemost_mail_state (
    id              SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    last_imap_uid   BIGINT NOT NULL DEFAULT 0,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO telemost_mail_state (id, last_imap_uid)
VALUES (1, 0)
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS telemost_mail_pending (
    id                  UUID PRIMARY KEY,
    imap_uid            TEXT NOT NULL,
    message_id          TEXT NOT NULL DEFAULT '',
    subject             TEXT NOT NULL DEFAULT '',
    sender              TEXT NOT NULL DEFAULT '',
    body_summary        TEXT NOT NULL DEFAULT '',
    transcript_text     TEXT NOT NULL DEFAULT '',
    classification      JSONB NOT NULL DEFAULT '{}'::jsonb,
    status              TEXT NOT NULL DEFAULT 'pending',
    chunks_count        INT NOT NULL DEFAULT 0,
    error_message       TEXT NOT NULL DEFAULT '',
    notify_chat_id      BIGINT NOT NULL DEFAULT 0,
    notify_topic_id     BIGINT NOT NULL DEFAULT 0,
    notify_message_id   BIGINT NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at         TIMESTAMPTZ,
    UNIQUE (imap_uid)
);

CREATE INDEX IF NOT EXISTS idx_telemost_mail_pending_status
    ON telemost_mail_pending (status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_telemost_mail_message_id
    ON telemost_mail_pending (message_id)
    WHERE message_id <> '';
