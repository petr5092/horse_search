from typing import Optional
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    PROJECT_NAME: str = "Horse Dataset Studio API"

    # PostgreSQL configuration (как в репозитории )
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

    # Private Yandex Object Storage; explicit static credentials are required.
    S3_ENDPOINT_URL: Optional[str] = "https://storage.yandexcloud.net"
    S3_BUCKET: str = "horse-dataset-vim"
    S3_REGION: str = "ru-central1"
    S3_ACCESS_KEY_ID: Optional[str] = None
    S3_SECRET_ACCESS_KEY: Optional[str] = None
    S3_SESSION_TOKEN: Optional[str] = None
    S3_ADDRESSING_STYLE: str = Field(default="path", pattern="^(path|virtual|auto)$")
    S3_OPERATION_LEASE_SECONDS: int = Field(default=600, ge=30)
    S3_MAX_FILE_SIZE: int = Field(default=100 * 1024 * 1024, gt=0, le=5 * 1024**3)
    S3_UPLOAD_EXPIRES_SECONDS: int = Field(default=900, ge=1, le=3600)
    S3_DOWNLOAD_EXPIRES_SECONDS: int = Field(default=300, ge=1, le=3600)

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
