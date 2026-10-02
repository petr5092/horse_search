from typing import Optional, Dict, Any, List
from sqlalchemy import text
from app.database import engine
from app.dao.base import BaseDAO


class ProjectDAO(BaseDAO):
    table_name: str = "projects"
