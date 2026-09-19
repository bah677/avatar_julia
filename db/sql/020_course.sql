-- Уроки и источники материалов контент-аватара.

CREATE TABLE IF NOT EXISTS course_lessons (
    id                  SERIAL PRIMARY KEY,
    product_id          TEXT NOT NULL,
    lesson_key          TEXT NOT NULL,
    module_no           INT,
    lesson_no           INT NOT NULL,
    title               TEXT NOT NULL DEFAULT '',
    disk_path           TEXT,
    passport            JSONB NOT NULL DEFAULT '{}'::jsonb,
    passport_text       TEXT NOT NULL DEFAULT '',
    passport_updated_at TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (product_id, lesson_key)
);

CREATE TABLE IF NOT EXISTS course_sources (
    id                UUID PRIMARY KEY,
    product_id        TEXT NOT NULL,
    origin            TEXT NOT NULL CHECK (origin IN ('disk', 'youtube', 'vimeo', 'kinescope')),
    kind              TEXT NOT NULL CHECK (kind IN (
                          'lesson_video', 'practice', 'broadcast', 'summary', 'slides',
                          'extra', 'other', 'post', 'expert_info', 'product_info')),
    lesson_id         INT REFERENCES course_lessons (id) ON DELETE SET NULL,
    title             TEXT NOT NULL DEFAULT '',
    disk_path         TEXT UNIQUE,
    disk_etag         TEXT NOT NULL DEFAULT '',
    video_id          TEXT,
    url               TEXT NOT NULL DEFAULT '',
    alt_urls          JSONB NOT NULL DEFAULT '[]'::jsonb,
    superseded_by     UUID REFERENCES course_sources (id) ON DELETE SET NULL,
    platform          TEXT NOT NULL DEFAULT '',
    duration_sec      INT,
    recorded_on       DATE,
    status            TEXT NOT NULL DEFAULT 'new' CHECK (status IN (
                          'new', 'fetching', 'extracted', 'indexed', 'mining',
                          'done', 'error', 'skipped', 'deleted')),
    attempts          INT NOT NULL DEFAULT 0,
    next_attempt_at   TIMESTAMPTZ,
    error_message     TEXT NOT NULL DEFAULT '',
    text_method       TEXT NOT NULL DEFAULT '',
    chars_count       INT NOT NULL DEFAULT 0,
    chunks_count      INT NOT NULL DEFAULT 0,
    cards_count       INT NOT NULL DEFAULT 0,
    intake_chat_id    BIGINT,
    intake_message_id BIGINT,
    added_by          BIGINT,
    metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at      TIMESTAMPTZ,
    UNIQUE (origin, video_id)
);

CREATE INDEX IF NOT EXISTS idx_course_sources_queue  ON course_sources (product_id, status, next_attempt_at, created_at);
CREATE INDEX IF NOT EXISTS idx_course_sources_lesson ON course_sources (lesson_id);
