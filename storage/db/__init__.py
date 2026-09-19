"""
Низкоуровневый слой работы с PostgreSQL.

Класс `Database` склеен из тематических mixin'ов (`users`, `payments`,
`messages`, …) в **`storage/db/database.py`**. В приложении к БД через пул
обращаются в основном через **`storage/user_storage.UserStorage`**
(наследник `Database`).
"""

from storage.db.database import Database

__all__ = ["Database"]
