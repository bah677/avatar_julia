-- Тикеты поддержки (`SupportMixin`, `bot/features/support.py`).

CREATE TABLE IF NOT EXISTS support_tickets (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,

    ticket_number VARCHAR(64) NOT NULL,
    topic TEXT NOT NULL,
    user_message TEXT NOT NULL,

    admin_response TEXT,
    admin_id BIGINT,

    status VARCHAR(32) NOT NULL DEFAULT 'open',
    -- ожидаемые значения: open, answered, closed, delivery_failed

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ,

    CONSTRAINT uq_support_tickets_number UNIQUE (ticket_number)
);

CREATE INDEX IF NOT EXISTS idx_support_tickets_user_created
    ON support_tickets (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_support_tickets_status_updated
    ON support_tickets (status, updated_at ASC)
    WHERE status = 'answered';
