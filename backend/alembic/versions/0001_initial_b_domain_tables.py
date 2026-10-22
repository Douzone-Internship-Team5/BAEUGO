"""Initial B domain tables

Revision ID: 0001_initial_b_domain_tables
Revises:
Create Date: 2026-10-22
"""

from alembic import op
from app.models import Base

# revision identifiers, used by Alembic.
revision = "0001_initial_b_domain_tables"
down_revision = "c18105b3bd65"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
