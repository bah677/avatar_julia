-- Артефакты админ-форума / ТЗ+КП / очереди ответов (старый проект). Безопасно на пустой БД.

DROP TABLE IF EXISTS tz_kp_client_shares CASCADE;
DROP TABLE IF EXISTS admin_mirror_messages CASCADE;
DROP TABLE IF EXISTS admin_responses CASCADE;

DROP INDEX IF EXISTS idx_users_admin_forum_thread;

ALTER TABLE users DROP COLUMN IF EXISTS admin_forum_message_thread_id;
ALTER TABLE users DROP COLUMN IF EXISTS admin_forum_topic_name;
ALTER TABLE users DROP COLUMN IF EXISTS admin_forum_topic_created_at;
ALTER TABLE users DROP COLUMN IF EXISTS admin_forum_topic_updated_at;
