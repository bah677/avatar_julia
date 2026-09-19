-- Карточки идей, извлечённые из источников.

CREATE TABLE IF NOT EXISTS content_cards (
    id                 UUID PRIMARY KEY,
    product_id         TEXT NOT NULL,
    source_id          UUID NOT NULL REFERENCES course_sources (id) ON DELETE CASCADE,
    lesson_id          INT REFERENCES course_lessons (id) ON DELETE SET NULL,
    type               TEXT NOT NULL,
    title              TEXT NOT NULL,
    text               TEXT NOT NULL,
    quote              TEXT NOT NULL DEFAULT '',
    anchor_sec         REAL,
    page               INT,
    speaker            TEXT NOT NULL DEFAULT 'expert',
    audience_pain      TEXT NOT NULL DEFAULT '',
    funnel_stage       TEXT NOT NULL DEFAULT 'warmup',
    formats            TEXT[] NOT NULL DEFAULT '{}',
    score              REAL NOT NULL DEFAULT 0,
    frequency          INT NOT NULL DEFAULT 1,
    related_source_ids UUID[] NOT NULL DEFAULT '{}',
    status             TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived', 'hidden')),
    used_count         INT NOT NULL DEFAULT 0,
    last_used_at       TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_content_cards_pick   ON content_cards (product_id, status, last_used_at NULLS FIRST, score DESC);
CREATE INDEX IF NOT EXISTS idx_content_cards_lesson ON content_cards (lesson_id);
