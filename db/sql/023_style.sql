-- Паспорт голоса и настройки контента по продукту.

CREATE TABLE IF NOT EXISTS style_profiles (
    id          BIGSERIAL PRIMARY KEY,
    product_id  TEXT NOT NULL,
    version     INT NOT NULL,
    text        TEXT NOT NULL,
    origin      TEXT NOT NULL CHECK (origin IN ('auto', 'manual')),
    source_hash TEXT NOT NULL DEFAULT '',
    is_active   BOOLEAN NOT NULL DEFAULT FALSE,
    created_by  BIGINT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (product_id, version)
);

CREATE TABLE IF NOT EXISTS content_settings (
    product_id  TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (product_id, key)
);
