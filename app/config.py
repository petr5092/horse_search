from typing import Optional
from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    PROJECT_NAME: str = "Horse Dataset Studio API"

    # PostgreSQL configuration (как в репозитории petr5092/FastAPI)
    POSTGRES_HOST: Optional[str] = None
    POSTGRES_PORT: Optional[int] = 5432
    POSTGRES_USER: Optional[str] = None
    POSTGRES_PASSWORD: Optional[str] = None
    POSTGRES_NAME: Optional[str] = None

    # Database URL for async SQLAlchemy
    DATABASE_URL: Optional[str] = None

    # JWT Authentication
    SECRET_KEY: str = "horse_dataset_vim_super_secret_jwt_key_2026_change_in_prod"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24

    # Initial superuser
    FIRST_SUPERUSER_EMAIL: str = "admin@vim.ru"
    FIRST_SUPERUSER_PASSWORD: str = "admin123"
    FIRST_SUPERUSER_NAME: str = "Иванов Алексей Михайлович"

    @model_validator(mode="after")
    def assemble_database_url(self):
        if not self.DATABASE_URL:
            if self.POSTGRES_HOST and self.POSTGRES_USER and self.POSTGRES_NAME:
                password_part = f":{self.POSTGRES_PASSWORD}" if self.POSTGRES_PASSWORD else ""
                self.DATABASE_URL = (
                    f"postgresql+asyncpg://{self.POSTGRES_USER}{password_part}@"
                    f"{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_NAME}"
                )
            else:
                self.DATABASE_URL = "sqlite+aiosqlite:///./horse_dataset.db"
        elif self.DATABASE_URL.startswith("sqlite:///"):
            self.DATABASE_URL = self.DATABASE_URL.replace("sqlite:///", "sqlite+aiosqlite:///")
        elif self.DATABASE_URL.startswith("postgresql://"):
            self.DATABASE_URL = self.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")
        return self

    class Config:
        env_file = ".env"
        extra = "allow"


settings = Settings()
