"""PostGIS and the schema-info table.

The extension is created here as well as by salt so a dev database built
from migrations alone matches production. CREATE EXTENSION needs a
superuser or a trusted extension; salt creates it on satweather before the
first migration runs, so on the host this is a no-op.

Revision ID: 0001
Revises:
Create Date: 2026-10-23
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")


def downgrade() -> None:
    pass
