"""
Композитный класс Database: собран из тематических mixin'ов (`storage/db/*.py`).

Публичная точка входа в приложении обычно — **`UserStorage`**
(`storage/user_storage.py`), наследующий этот класс.
"""

from storage.db._base import DatabaseBase
from storage.db.bot_admins import BotAdminsMixin
from storage.db.creative_sessions import CreativeSessionsMixin
from storage.db.forum_topic_names import ForumTopicNamesMixin
from storage.db.gifts import GiftsMixin
from storage.db.licenses import LicensesMixin
from storage.db.media_archive import MediaArchiveMixin
from storage.db.messages import MessagesMixin
from storage.db.orders import OrdersMixin
from storage.db.payments import PaymentsMixin
from storage.db.referrals import ReferralsMixin
from storage.db.support import SupportMixin
from storage.db.tariffs import TariffsMixin
from storage.db.users import UsersMixin
from storage.db.yandex_disk import YandexDiskMixin
from storage.db.telemost_mail import TelemostMailMixin
from storage.db.rag_import_cache import RagImportCacheMixin
from storage.db.rag_source_visibility import RagSourceVisibilityMixin
from storage.db.course import CourseMixin
from storage.db.content_cards import ContentCardsMixin
from storage.db.content_items import ContentItemsMixin
from storage.db.style import StyleMixin


class Database(
    ForumTopicNamesMixin,
    CreativeSessionsMixin,
    BotAdminsMixin,
    UsersMixin,
    MessagesMixin,
    SupportMixin,
    PaymentsMixin,
    LicensesMixin,
    OrdersMixin,
    TariffsMixin,
    ReferralsMixin,
    GiftsMixin,
    MediaArchiveMixin,
    YandexDiskMixin,
    TelemostMailMixin,
    RagSourceVisibilityMixin,
    RagImportCacheMixin,
    CourseMixin,
    ContentCardsMixin,
    ContentItemsMixin,
    StyleMixin,
    DatabaseBase,
):
    """Единая точка доступа к PostgreSQL.

    Каждый mixin отвечает за свою предметную область:
      - ForumTopicNamesMixin  — forum_topic_names (кэш имён топиков для RAG)
      - CreativeSessionsMixin — creative_sessions, creative_task_turns (/new)
      - BotAdminsMixin        — bot_admins (доступ без лицензии; управление через суперадмина)
      - UsersMixin            — users (профиль, активность, бан, сессия агента)
      - MessagesMixin         — messages, token_usage, interaction_logs
      - SupportMixin          — support_tickets
      - PaymentsMixin         — payments
      - LicensesMixin         — license (включая бонусные продления)
      - OrdersMixin           — orders (включая подарочные)
      - TariffsMixin          — tariffs + tariff_prices
      - ReferralsMixin        — referrals + ref_keys
      - GiftsMixin            — gifts
      - MediaArchiveMixin     — media_inbound_files
      - CourseMixin           — course_lessons, course_sources
      - ContentCardsMixin     — content_cards
      - ContentItemsMixin     — content_plans/items/versions/feedback
      - StyleMixin            — style_profiles, content_settings
      - DatabaseBase          — пул подключений и get_connection()
    """
