"""merge onboarding and instructor

Revision ID: d9aa8dbe4bb9
Revises: 15c93b5cbeeb, 4980b32f765f
"""

from collections.abc import Sequence

revision: str = "d9aa8dbe4bb9"
down_revision: str | Sequence[str] | None = ("15c93b5cbeeb", "4980b32f765f")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
