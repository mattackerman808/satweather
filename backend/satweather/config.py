"""Runtime settings, read from the environment (SATWEATHER_*).

On satweather the values come from /etc/satweather/satweather.env, which
salt renders from pillar (salt/satweather). Defaults match that layout so a
dev box only needs SATWEATHER_DATABASE_URL.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SATWEATHER_")

    # postgresql+psycopg://satweather:<pw>@127.0.0.1/satweather
    database_url: str = "postgresql+psycopg:///satweather"
    # Where satdump writes on rtl, seen here through the read-only NFS mount.
    data_root: Path = Path("/mnt/satImages/goes-hrit")
    # Derived images (web-sized copies, thumbnails, map tiles). Local disk.
    media_root: Path = Path("/srv/satweather/media")


@lru_cache
def get_settings() -> Settings:
    return Settings()
