from sqlalchemy import create_engine, text
from app.config import settings

# Synchronous engine for raw SQL queries
connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    echo=False
)


def init_db():
    """Initializes tables using pure raw SQL (DDL)."""
    create_users_table = """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email VARCHAR(255) UNIQUE NOT NULL,
        hashed_password VARCHAR(255) NOT NULL,
        full_name VARCHAR(255) NOT NULL,
        role VARCHAR(50) NOT NULL DEFAULT 'annotator',
        is_active BOOLEAN NOT NULL DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """
    create_projects_table = """
    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name VARCHAR(255) NOT NULL,
        schema_version VARCHAR(50) NOT NULL DEFAULT '1.0',
        target_size INTEGER NOT NULL DEFAULT 640,
        description TEXT,
        created_by INTEGER REFERENCES users(id),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """
    with engine.begin() as conn:
        conn.execute(text(create_users_table))
        conn.execute(text(create_projects_table))
