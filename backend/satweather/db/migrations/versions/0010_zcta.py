"""ZIP codes for place search: the Census ZIP Code Tabulation Areas'
internal points (satweather-refdata --only zcta).

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-04
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE zcta (
            zip   text PRIMARY KEY,
            geom  geometry(Point, 4326) NOT NULL
        );
        CREATE INDEX zcta_zip_prefix ON zcta (zip text_pattern_ops);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS zcta")
