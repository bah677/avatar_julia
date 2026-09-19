-- Учёт проиндексированных файлов с Яндекс.Диска (дедуп по etag/пути).

CREATE TABLE IF NOT EXISTS yandex_disk_indexed_files (
    id              BIGSERIAL PRIMARY KEY,
    source_id       TEXT NOT NULL,
    remote_path     TEXT NOT NULL,
    file_name       TEXT NOT NULL DEFAULT '',
    etag            TEXT NOT NULL DEFAULT '',
    file_size       BIGINT NOT NULL DEFAULT 0,
    chunks_count    INT NOT NULL DEFAULT 0,
    indexed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (source_id, remote_path)
);

CREATE INDEX IF NOT EXISTS idx_yandex_disk_indexed_source
    ON yandex_disk_indexed_files (source_id);

CREATE INDEX IF NOT EXISTS idx_yandex_disk_indexed_at
    ON yandex_disk_indexed_files (indexed_at DESC);
