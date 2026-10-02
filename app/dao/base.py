from typing import Any, Dict, List, Optional
from sqlalchemy import text
from app.database import engine


class BaseDAO:
    table_name: str = ""

    @classmethod
    def find_by_id(cls, model_id: int) -> Optional[Dict[str, Any]]:
        sql = f"SELECT * FROM {cls.table_name} WHERE id = :id"
        with engine.connect() as conn:
            result = conn.execute(text(sql), {"id": model_id})
            row = result.mappings().one_or_none()
            return dict(row) if row else None

    @classmethod
    def find_one_or_none(cls, **filter_by) -> Optional[Dict[str, Any]]:
        where_clauses = [f"{col} = :{col}" for col in filter_by.keys()]
        where_str = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        sql = f"SELECT * FROM {cls.table_name} {where_str} LIMIT 1"

        with engine.connect() as conn:
            result = conn.execute(text(sql), filter_by)
            row = result.mappings().one_or_none()
            return dict(row) if row else None

    @classmethod
    def find_all(cls, **filter_by) -> List[Dict[str, Any]]:
        where_clauses = [f"{col} = :{col}" for col in filter_by.keys()]
        where_str = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        sql = f"SELECT * FROM {cls.table_name} {where_str} ORDER BY id ASC"

        with engine.connect() as conn:
            result = conn.execute(text(sql), filter_by)
            rows = result.mappings().all()
            return [dict(r) for r in rows]

    @classmethod
    def add(cls, **data) -> Dict[str, Any]:
        columns = list(data.keys())
        cols_str = ", ".join(columns)
        vals_str = ", ".join([f":{col}" for col in columns])

        sql = f"INSERT INTO {cls.table_name} ({cols_str}) VALUES ({vals_str})"

        with engine.begin() as conn:
            result = conn.execute(text(sql), data)
            inserted_id = result.lastrowid

        return cls.find_by_id(inserted_id)

    @classmethod
    def update_by_id(cls, model_id: int, **data) -> Optional[Dict[str, Any]]:
        if not data:
            return cls.find_by_id(model_id)

        set_clauses = [f"{col} = :{col}" for col in data.keys()]
        set_str = ", ".join(set_clauses)
        sql = f"UPDATE {cls.table_name} SET {set_str} WHERE id = :target_id"

        params = {**data, "target_id": model_id}

        with engine.begin() as conn:
            conn.execute(text(sql), params)

        return cls.find_by_id(model_id)

    @classmethod
    def count(cls, **filter_by) -> int:
        where_clauses = [f"{col} = :{col}" for col in filter_by.keys()]
        where_str = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        sql = f"SELECT COUNT(*) AS total_count FROM {cls.table_name} {where_str}"

        with engine.connect() as conn:
            result = conn.execute(text(sql), filter_by)
            row = result.mappings().one_or_none()
            return int(row["total_count"]) if row else 0
