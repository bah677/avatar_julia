# Схема PostgreSQL (с нуля)

Скрипты нумеруются по порядку применения. База создаётся пустой; миграций со старых схем нет.

При добавлении фич дополняйте новыми файлами `0NN_*.sql` и кратко укажите в этом README, какие модули `storage/db/*` они обслуживают.

| Файл | Назначение |
|------|------------|
| `001_extensions.sql` | Расширения (опционально jsonb и т.д. — сейчас заглушка). |
| `002_users.sql` | `UsersMixin`, профиль, бан. |
| `003_license.sql` | `LicensesMixin`, белый список доступа. |
| `004_license_history.sql` | История изменений лицензий. |
| `005_messages.sql` | Переписка и история для LLM (`MessageCopier`, `get_private_chat_history`). |
| `006_interaction_logs.sql` | `InboundLoggingMiddleware`, `log_interaction`. |
| `007_token_usage.sql` | Учёт токенов LLM (`log_llm_completion_usage`). Поле `created_date` — generated для агрегатов по дням. |
| `008_bot_admins.sql` | Таблица `bot_admins` (доступ без лицензии; управление командами `/admin_add`, `/admin_block` от `SUPER_ADMIN_ID`). |
| `009_drop_legacy_admin_forum.sql` | Удаление таблиц/колонок старого админ-форума и ТЗ+КП (идемпотентно). |
| `010_support_tickets.sql` | `SupportMixin`, тикеты `/support` и цикл «answered» → пользователь. |
| `011_media_inbound_files.sql` | `MediaArchiveMixin`, входящие медиа (`MEDIA_INBOUND_ARCHIVE_*`). |
| `012_forum_topic_names.sql` | `ForumTopicNamesMixin`, кэш имён форум-топиков для RAG (`group_rag_indexer`). |
| `013_creative_sessions.sql` | `CreativeSessionsMixin`, сессия /new и ходы задачи (`creative_task_turns`). |
| `014_yandex_disk_indexed.sql` | `YandexDiskMixin`, кэш проиндексированных файлов Диска (старый контур). |
| `015_telemost_mail.sql` | `TelemostMailMixin` (фича в этой сборке не регистрируется). |
| `016_rag_source_visibility.sql` | видимость источников RAG (фича не регистрируется). |
| `017_rag_import_cache.sql` | `RagImportCacheMixin`. |
| `018_telemost_mail_recordings.sql` | записи Телемоста (фича не регистрируется). |
| `019_telemost_recordings_audio.sql` | аудио записей Телемоста. |
| `020_course.sql` | `CourseMixin`: `course_lessons`, `course_sources`. |
| `021_content_cards.sql` | `ContentCardsMixin`: банк карточек идей. |
| `022_content_items.sql` | `ContentItemsMixin`: планы недели, черновики, версии, обратная связь. |
| `023_style.sql` | `StyleMixin`: `style_profiles`, `content_settings`. |

В `.env`: `SUPER_ADMIN_ID=<telegram_user_id>` — полный доступ и единственный, кто добавляет/удаляет записи в `bot_admins`.

Применение подряд (или скрипт [`scripts/init_db_schema.sh`](../scripts/init_db_schema.sh) под **sudo** из каталога `avatar`: `sudo ./scripts/init_db_schema.sh` — читает `./.env`; опционально первый аргумент — **полный путь** к другому `.env`):

```bash
psql -v ON_ERROR_STOP=1 "$DATABASE_URL" -f db/sql/001_extensions.sql
# … 002 … 023; при обновлении со старой схемы — также 009
# контент-аватар: обязательно 020–023
```

После применения выдайте тестовую лицензию вручную, например:

```sql
INSERT INTO license (user_id, license_type, expires_at, status)
VALUES (123456789, 'subscription', NOW() + INTERVAL '365 days', 'active');
```
