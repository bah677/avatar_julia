# БиблияБот — мануал для разработки и ИИ-агентов

Корень процесса: `/home/appuser/bog/biblia` (dev). Боевой каталог на том же сервере: `/home/appuser/biblia`. Этот документ описывает архитектуру, слои, «подводные камни» и типовые операции.

---

## 1. Назначение и стек

- **Telegram-бот** (aiogram 3.x): диалог с пользователем на основе Писания, донаты, поддержка, рефералка, рассылки.
- **PostgreSQL** (asyncpg): пользователи, сообщения, платежи, лицензии, рассылки и т.д.
- **DeepSeek** (`DEEPSEEK_API_KEY`, OpenAI-совместимый API) — основной LLM для ответов; **`AgentsClient`** в `openai_client/agents_client.py`.
- **OpenAI API** (`OPENAI_API_KEY`) — мини-модели для вспомогательных задач (например телеграм-HTML из рассылок, часть `openai_client.assistant`).
- **Парсинг входящих**: `parse_mode` HTML в ответах; история и логирование в таблице `messages`.

---

## 2. Карта каталогов (важное)

| Путь | Роль |
|------|------|
| `main.py` | Точка входа: логирование в `log/biblia_bot.log`, `validate_biblia_bot_startup`, `BotApplication`, `bot.start()` (polling). |
| `bot_app.py` | Класс `BotApplication`: регистрирует фичи Biblia, хендлеры, `MessageHandlers`. |
| `config.py` | `BibliaBotConfig` / `load_biblia_bot_config`, `validate_biblia_bot_startup`; общий `AppConfig` (`config`) для полей клуба/YooKassa, даже если в Библии не используются. `.env` грузится из каталога проекта. |
| `command_handlers.py` | Команды `/start`, `/support`, `/payment`, `/donat`, `/affiliate`; рефералка по `/start ref_<id>`. |
| `bot/base_app.py` | Абстрактный каркас: `Bot`, `Dispatcher`, `UserStorage`, очередь сообщений на пользователя, middleware, `FeatureManager`, медиапроцессор, опционально BZB. |
| `bot/features/` | Фичи: `messaging`, `payment`, `mailing`, `scheduled_mailing`, `scripture_encouragement_mailing`, `support`, `referral_program`, и т.д. Каждая наследует `BaseFeature`, имеет `name`, `register_handlers`, `initialize`. |
| `bot/handlers/messages.py` | Универсальный хендлер сообщений, медиапроцессор, маршрутизация в фичи (`route_message_to_feature`). |
| `bot/middleware/` | `InboundLoggingMiddleware` (лог в БД **до** хендлеров), `AccessControlMiddleware` (бан), `OutgoingLoggingMiddleware`, опционально `GroupChatHygieneMiddleware`. |
| `bot/logging/` | **`message_copier.py`** обязателен: копирование сообщений в `messages`; **`interaction_logger.py`**. Пакет называется **`bot.logging`** — не путать со stdlib `logging`. |
| `bot/payments/` | YooKassa, BZB, проверка платежей, валюты. |
| `bot/media_processing/` | Голос, фото, документы → текст/метаданные перед диалогом. |
| `storage/user_storage.py` | Публичное имя для БД: класс `UserStorage` = композит **`Database`**; свойство `.db` возвращает `self` (shim). |
| `storage/db/database.py` | Класс `Database` = множество **mixin**'ов (`users`, `messages`, `payments`, `referrals`, …) + `DatabaseBase` (пул). |
| `storage/mailing_storage.py` | Кампании и аудитория рассылок (`mailing_campaigns`, `mailing_audience`, источники кампаний). |
| `storage/scheduled_mailing_storage.py` | Расписания LLM-благословений, выборка пользователей для цитат/благословений. |
| `storage/mailing_storage.py` | Константы `CAMPAIGN_SOURCE_*`, создание кампаний, recover «зависших» отправок. |
| `openai_client/` | `agents_client.py` (DeepSeek + история), `assistant.py`, `scripture_prompt.py`. |
| `migrations/biblia/` | SQL-миграции; на проде применяются вручную или скриптами (без единого авто-раннера в коде). |
| `scripts/` | Например `deploy_prod.sh` — накат кода dev→prod (см. §8). |

---

## 3. Поток запроса (средство проследить баг)

1. **Polling** получает `Update`.
2. **Middleware (порядок важен):**  
   - при `CLUB_GROUP_ID` — `GroupChatHygieneMiddleware`;  
   - **`InboundLoggingMiddleware`** — пишет входящие в `messages` / `interaction_logs` (**раньше** хендлеров);  
   - **`AccessControlMiddleware`** — бан/доступ.
3. Хендлеры (`MessageHandlers`) → медиапроцессинг → **`add_to_queue`** / обработка.
4. **Очередь по `user_id`** (`base_app._process_user_messages`) → **`route_message_to_feature`**: при FSM support → `support`, иначе → **`messaging`** (`ScriptureMessagingFeature`).
5. Исходящие в Telegram проходят через **`OutgoingLoggingMiddleware`** (лог исходящих в `messages`).

**Критично:** в `MessageCopier.save_incoming` перед `INSERT INTO messages` вызывается **`add_or_update_user`**, иначе FK `messages.user_id → users` падает: middleware логирования выполняется до сохранения пользователя в хендлерах.

---

## 4. Фичи Biblia (`bot_app.py`)

Регистрируются в `FeatureManager` под строковыми именами (`feature.name`), например:

- **`messaging`** — `ScriptureMessagingFeature`: основной диалог, DeepSeek, история.
- **`support`** — тикеты.
- **`referral`** / рефералка — `ReferralProgramFeature` (ссылки `/affiliate`, `ref_` в `/start`).
- **`payment`** — донаты YooKassa/BZB.
- **`mailing`** — очередь отправки кампаний из БД (воркер по таймеру).
- **`scheduled_mailing`** — **ежедневное благословение ~10:00 MSK**, промпт из `mailing_schedules`, LLM HTML.
- **`scripture_encouragement_mailing`** — **ежедневные цитаты ~8:00 MSK**, отдельная кампания и аудитория.
- **`faq`**, **`media_id_helper`**, и т.д.

Доступ: `feature_manager.get("messaging")`, опционально `get_optional("club_group")`.

---

## 5. Рассылки (нюансы)

- **Цитаты:** `ScriptureEncouragementMailingFeature` — APScheduler, cron **08:00 `Europe/Moscow`**, кампания с источником **`scripture_encouragement`**.
- **Благословения:** `ScheduledMailingFeature` — cron **10:00 MSK**, промпт из активных **`mailing_schedules`**; источник кампании **`scheduled_mailing_daily`**.
- **`MailingFeature`** опрашивает БД (например каждые 60 с) и шлёт батчами готовые кампании со статусом *ready/planned* (см. код фичи и `mailing_storage`).

**Частая поломка после `pg_dump`/`pg_restore`:** последовательность **`mailing_campaigns_id_seq`** отстаёт от `MAX(id)` → второй INSERT в тот же день даёт `duplicate key` на PK. Лечение на БД:

```sql
SELECT setval(
  'mailing_campaigns_id_seq',
  (SELECT COALESCE(MAX(id), 0) FROM mailing_campaigns)
);
```

---

## 6. Конфигурация окружения

Обязательные для старта (см. `validate_biblia_bot_startup` в `config.py`):

- `BIBLIA_BOT_TOKEN`, `BIBLIA_DB_NAME` (или `DB_NAME`), `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `OPENAI_API_KEY`, **`DEEPSEEK_API_KEY`**.

Часто используются (часть в `AppConfig`): `TELEGRAM_BOT_USERNAME` (ссылки), YooKassa, `BZB_API_KEY`, `LOG_LEVEL`, клубные поля могут быть нулями.

`.env` лежит **рядом с `config.py`**; запускать бота лучше из корня проекта или полагаться на абсолютный путь в `load_dotenv`.

---

## 7. База данных

- Роль приложения на проде обычно **`biblia_bot_user`**; dev-клон может быть отдельной БД и пользователем.
- **`UserStorage.database_url`** = `postgresql://USER:PASS@HOST:PORT/BIBLIA_DB_NAME`.
- Крупные таблицы: `users`, **`messages`** (история + лог), `payments`, `license`, `mailing_*`, `referrals`, `interaction_logs`.

После копирования БД всегда проверяйте **sequences** для таблиц с `SERIAL`/`IDENTITY`, не только `mailing_campaigns`.

---

## 8. Dev / prod на одном сервере

- **Dev:** `/home/appuser/bog/biblia`
- **Prod:** `/home/appuser/biblia`

Типичный накат кода на прод (если лежит `scripts/deploy_prod.sh`): rsync каталогов кода из dev, **без** копирования `venv/`, без перезаписи `.env` по умолчанию, опционально `--sql` для миграций, затем **`sudo supervisorctl restart bots:biblia_bot`** (имя группы может отличаться — смотреть `supervisorctl status`).

**venv** на prod и dev собирают **отдельно** из `requirements.txt`; не копировать venv между машинами/каталогами с разными путями.

Полезно держать в dev полный набор файлов, включая **`bot/logging/message_copier.py`** — при неполном зеркале с прода файл мог пропасть и импорт `bot.logging.message_copier` сломается.

---

## 9. Тестовый / ручной запуск

```bash
cd /home/appuser/bog/biblia
source venv/bin/activate   # или ./venv/bin/python
python main.py
```

Логи: консоль + `log/biblia_bot.log`.

---

## 10. Соглашения по коду

- Новые фичи: наследник `BaseFeature`, зарегистрировать в `bot_app._register_features`, при необходимости добавить ветку в `route_message_to_feature` или команды в `command_handlers`.
- Работа с БД: через **`UserStorage`** / методы mixin'ов; не плодить сырые SQL в хендлерах без нужды.
- HTML в Telegram: ответы ассистента проходят через нормализацию HTML (см. `AgentsClient`, `mailing_llm_html_async`).
- Парсинг: исходящие с `parse_mode=HTML` — следить за экранированием (`html.escape` в рефералке и т.д.).

---

## 11. Чек-лист для ИИ-агента при задаче «починить X»

1. Ошибка в логе `log/biblia_bot.log` или supervisor-логе?
2. Это **middleware до users row**, **FK**, **рассылка/sequence**, **платёж**, **LLM**?
3. После изменений в БД — нужен ли **`setval`** для последовательностей?
4. Менялись ли пути **dev/prod** и не потерян ли файл (например `message_copier.py`)?
5. Для рассылок: два независимых cron (8:00 / 10:00) и отдельные `campaign_source`.

---

## 12. Связанные файлы-якоря

- Жизненный цикл приложения: `bot/base_app.py` (`initialize`, middleware, `start`).
- Сборка Biblia: `bot_app.py`.
- Диалог: `bot/features/scripture_messaging.py`.
- Рассылки: `bot/features/mailing.py`, `scheduled_mailing.py`, `scripture_encouragement_mailing.py`.
- Память диалога и токены: `openai_client/agents_client.py`, `storage/db/messages.py`.
- Рефералка: `bot/features/referral_program.py`, `command_handlers.py` (`ref_`).

---

*Документ можно дополнять по мере появления новых фич; при крупных изменениях в схеме БД обновляйте §5 и §7.*
