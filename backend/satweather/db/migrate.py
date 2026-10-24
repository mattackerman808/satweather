"""`satweather-migrate`: bring the schema to head.

Salt runs `satweather-migrate --check` as the `unless` of the migration
step, so a highstate only reports a change when there was a migration to
apply. Both modes must work without a working directory: the Alembic
config ships inside the package.
"""

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from satweather.config import get_settings
from satweather.db import get_engine

HERE = Path(__file__).resolve().parent


def alembic_config() -> Config:
    cfg = Config(str(HERE / "alembic.ini"))
    cfg.set_main_option("script_location", str(HERE / "migrations"))
    # Alembic's ini interpolation treats % specially.
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))
    return cfg


def at_head(cfg: Config) -> bool:
    heads = set(ScriptDirectory.from_config(cfg).get_heads())
    with get_engine().connect() as conn:
        current = set(MigrationContext.configure(conn).get_current_heads())
    return current == heads


def main() -> None:
    cfg = alembic_config()
    if "--check" in sys.argv[1:]:
        sys.exit(0 if at_head(cfg) else 1)
    command.upgrade(cfg, "head")


if __name__ == "__main__":
    main()
