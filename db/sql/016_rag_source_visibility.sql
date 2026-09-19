-- Публичные / приватные ссылки на источники RAG (уровень группы TG, папки Я.Диска).

CREATE TABLE IF NOT EXISTS rag_source_visibility (
    source_type     TEXT NOT NULL,
    source_key      TEXT NOT NULL,
    visibility      TEXT NOT NULL CHECK (visibility IN ('public', 'private')),
    label           TEXT NOT NULL DEFAULT '',
    decided_by      BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (source_type, source_key)
);

CREATE TABLE IF NOT EXISTS rag_source_visibility_pending (
    id              UUID PRIMARY KEY,
    source_type     TEXT NOT NULL,
    source_key      TEXT NOT NULL,
    label           TEXT NOT NULL DEFAULT '',
    notify_sent     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_type, source_key)
);
