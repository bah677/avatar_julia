-- Письма с записью эфира (отдельно от конспекта TXT); связь по meeting_id.

CREATE TABLE IF NOT EXISTS telemost_mail_recordings (
    meeting_id      TEXT PRIMARY KEY,
    imap_uid        TEXT NOT NULL DEFAULT '',
    message_id      TEXT NOT NULL DEFAULT '',
    subject         TEXT NOT NULL DEFAULT '',
    video_url       TEXT NOT NULL DEFAULT '',
    local_path      TEXT NOT NULL DEFAULT '',
    linked_pending_id UUID,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    downloaded_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_telemost_mail_recordings_pending
    ON telemost_mail_recordings (linked_pending_id)
    WHERE linked_pending_id IS NOT NULL;
