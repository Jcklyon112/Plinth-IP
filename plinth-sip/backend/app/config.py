import os
from pydantic_settings import BaseSettings
from pathlib import Path


# Resolve paths relative to the backend directory
_BACKEND_DIR = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIGS = str(_BACKEND_DIR.parent / "configs")


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+psycopg://plinth:plinth_dev@localhost:5432/plinth_sip"
    ENV: str = "development"
    CONFIGS_DIR: str = _DEFAULT_CONFIGS
    ANTHROPIC_API_KEY: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()

# Ensure the key lands in os.environ so libraries that call
# os.environ.get("ANTHROPIC_API_KEY") directly (LangGraph, langchain-anthropic) find it.
if settings.ANTHROPIC_API_KEY and not os.environ.get("ANTHROPIC_API_KEY"):
    os.environ["ANTHROPIC_API_KEY"] = settings.ANTHROPIC_API_KEY
