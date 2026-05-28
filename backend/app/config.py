from pathlib import Path

from pydantic_settings import BaseSettings


_BACKEND_DIR = Path(__file__).resolve().parent.parent
_DEFAULT_SQLITE_PATH = _BACKEND_DIR / "rent_calculator.db"


class Settings(BaseSettings):
    DATABASE_URL: str = f"sqlite:///{_DEFAULT_SQLITE_PATH}"
    ENV: str = "development"
    RENTCAST_API_KEY: str = ""
    HUD_API_TOKEN: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
