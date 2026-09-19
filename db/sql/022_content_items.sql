-- Планы недели, черновики и обратная связь.

CREATE TABLE IF NOT EXISTS content_plans (
    id          UUID PRIMARY KEY,
    product_id  TEXT NOT NULL,
    week_start  DATE NOT NULL,
    focus       TEXT NOT NULL DEFAULT '',
    mix         JSONB NOT NULL DEFAULT '{}'::jsonb,
    chat_id     BIGINT,
    message_id  BIGINT,
    created_by  BIGINT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS content_items (
    id              UUID PRIMARY KEY,
    product_id      TEXT NOT NULL,
    plan_id         UUID REFERENCES content_plans (id) ON DELETE SET NULL,
    format          TEXT NOT NULL,
    card_ids        UUID[] NOT NULL DEFAULT '{}',
    lesson_id       INT REFERENCES course_lessons (id) ON DELETE SET NULL,
    angle           TEXT NOT NULL DEFAULT '',
    goal            TEXT NOT NULL DEFAULT '',
    planned_day     SMALLINT,
    status          TEXT NOT NULL DEFAULT 'planned' CHECK (status IN (
                        'planned', 'skipped', 'drafting', 'draft',
                        'approved', 'rejected', 'published')),
    current_version INT NOT NULL DEFAULT 0,
    created_by      BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS content_versions (
    id                BIGSERIAL PRIMARY KEY,
    item_id           UUID NOT NULL REFERENCES content_items (id) ON DELETE CASCADE,
    version           INT NOT NULL,
    text              TEXT NOT NULL,
    instruction       TEXT NOT NULL DEFAULT '',
    model             TEXT NOT NULL DEFAULT '',
    prompt_tokens     INT NOT NULL DEFAULT 0,
    completion_tokens INT NOT NULL DEFAULT 0,
    chat_id           BIGINT,
    message_ids       BIGINT[] NOT NULL DEFAULT '{}',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (item_id, version)
);

CREATE TABLE IF NOT EXISTS content_feedback (
    id          BIGSERIAL PRIMARY KEY,
    item_id     UUID NOT NULL REFERENCES content_items (id) ON DELETE CASCADE,
    version     INT NOT NULL,
    user_id     BIGINT,
    kind        TEXT NOT NULL CHECK (kind IN ('up', 'down', 'published')),
    reason_code TEXT NOT NULL DEFAULT '',
    reason_text TEXT NOT NULL DEFAULT '',
    folded      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_content_items_product ON content_items (product_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_content_versions_msg ON content_versions (chat_id, created_at DESC);
