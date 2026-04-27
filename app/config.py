import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse


def load_env_file(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    app_name: str = "Taste Graph v1"
    plex_base_url: Optional[str] = None
    plex_token: Optional[str] = None
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4o-mini"
    database_url: str = "sqlite:///data/taste_graph.sqlite"

    @property
    def sqlite_path(self) -> Path:
        parsed = urlparse(self.database_url)
        if parsed.scheme != "sqlite":
            raise ValueError("Only sqlite DATABASE_URL values are supported in v1.")
        raw_path = parsed.path
        if raw_path.startswith("/") and not self.database_url.startswith("sqlite:////"):
            raw_path = raw_path.lstrip("/")
        return Path(raw_path)


@lru_cache
def get_settings() -> Settings:
    load_env_file()
    return Settings(
        app_name=os.getenv("APP_NAME", "Taste Graph v1"),
        plex_base_url=os.getenv("PLEX_BASE_URL"),
        plex_token=os.getenv("PLEX_TOKEN"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        database_url=os.getenv("DATABASE_URL", "sqlite:///data/taste_graph.sqlite"),
    )
