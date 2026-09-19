-- Учёт токенов LLM (log_llm_completion_usage / add_token_usage_with_metadata).

CREATE TABLE IF NOT EXISTS token_usage (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    message_id TEXT,
    model TEXT NOT NULL,
    prompt_tokens INT NOT NULL DEFAULT 0,
    completion_tokens INT NOT NULL DEFAULT 0,
    total_tokens INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- только IMMUTABLE-выражения; cast(tstz AS date) зависит от TimeZone и не подходит
    created_date DATE GENERATED ALWAYS AS ((created_at AT TIME ZONE 'UTC')::date) STORED,

    request_id TEXT,
    thread_id TEXT,
    duration_sec INT,
    metadata JSONB,

    provider TEXT NOT NULL DEFAULT 'openai',
    request_kind TEXT,
    raw_usage JSONB,
    cached_input_tokens BIGINT,
    reasoning_output_tokens BIGINT
);

CREATE INDEX IF NOT EXISTS idx_token_usage_user_created ON token_usage (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_token_usage_created_date ON token_usage (created_date DESC);
CREATE INDEX IF NOT EXISTS idx_token_usage_provider_date ON token_usage (provider, created_date DESC);
