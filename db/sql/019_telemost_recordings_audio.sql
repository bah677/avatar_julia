-- Аудио-запись эфира (ссылка из письма + локальный файл).

ALTER TABLE telemost_mail_recordings
    ADD COLUMN IF NOT EXISTS audio_url TEXT NOT NULL DEFAULT '';

ALTER TABLE telemost_mail_recordings
    ADD COLUMN IF NOT EXISTS local_audio_path TEXT NOT NULL DEFAULT '';

ALTER TABLE telemost_mail_recordings
    ADD COLUMN IF NOT EXISTS audio_downloaded_at TIMESTAMPTZ;
