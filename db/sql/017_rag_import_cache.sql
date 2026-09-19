-- Кэш импорта в RAG: не индексировать повторно уже обработанное.

CREATE TABLE IF NOT EXISTS rag_import_cache (
    import_type     TEXT NOT NULL,
    cache_key       TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'indexed',
    chunks_count    INT NOT NULL DEFAULT 0,
    label           TEXT NOT NULL DEFAULT '',
    error_message   TEXT NOT NULL DEFAULT '',
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (import_type, cache_key)
);

CREATE INDEX IF NOT EXISTS idx_rag_import_cache_status
    ON rag_import_cache (import_type, status);
