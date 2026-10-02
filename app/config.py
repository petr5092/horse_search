from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    PROJECT_NAME: str = "Horse Dataset Studio API"
    DATABASE_URL: str = "sqlite:///./horse_dataset.db"

    SECRET_KEY: str = "horse_dataset_vim_super_secret_jwt_key_2026_change_in_prod"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24

    FIRST_SUPERUSER_EMAIL: str = "admin@vim.ru"
    FIRST_SUPERUSER_PASSWORD: str = "admin123"
    FIRST_SUPERUSER_NAME: str = "Иванов Алексей Михайлович"

    class Config:
        env_file = ".env"


settings = Settings()
