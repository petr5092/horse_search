from typing import Optional, Dict, Any
from sqlalchemy import text
from app.database import engine
from app.dao.base import BaseDAO


class UserDAO(BaseDAO):
    table_name: str = "users"

    @classmethod
    def find_by_email(cls, email: str) -> Optional[Dict[str, Any]]:
        sql = f"SELECT * FROM {cls.table_name} WHERE email = :email LIMIT 1"
        with engine.connect() as conn:
            result = conn.execute(text(sql), {"email": email.lower().strip()})
            row = result.mappings().one_or_none()
            return dict(row) if row else None
