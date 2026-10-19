"""Separate project display names from storage identifiers."""
from alembic import op
import sqlalchemy as sa

revision = "20261011_0030"
down_revision = "20260925_0029"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("projects", sa.Column("storage_slug", sa.String(96), nullable=True))


def downgrade():
    op.drop_column("projects", "storage_slug")
