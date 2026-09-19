-- Админы бота: полный доступ без лицензии. Назначает только SUPER_ADMIN_ID из .env (команды /admin_add, /admin_block).

CREATE TABLE IF NOT EXISTS bot_admins (
    user_id BIGINT PRIMARY KEY,
    added_by BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE bot_admins IS 'Доп. администраторы; суперадмин только в env SUPER_ADMIN_ID';
CREATE INDEX IF NOT EXISTS idx_bot_admins_added_by ON bot_admins (added_by) WHERE added_by IS NOT NULL;
