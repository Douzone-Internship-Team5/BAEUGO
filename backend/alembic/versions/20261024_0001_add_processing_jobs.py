"""add processing jobs

Revision ID: 7e5c91a2d804
Revises: d9aa8dbe4bb9
"""

import sqlalchemy as sa

from alembic import op

revision = "7e5c91a2d804"
down_revision = "d9aa8dbe4bb9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "processing_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "course_id",
            sa.BigInteger(),
            sa.ForeignKey("courses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("video_id", sa.BigInteger(), sa.ForeignKey("videos.id", ondelete="CASCADE")),
        sa.Column("section_id", sa.BigInteger(), sa.ForeignKey("sections.id", ondelete="CASCADE")),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("input_snapshot", sa.JSON()),
        sa.Column("artifacts", sa.JSON()),
        sa.Column("error_code", sa.String(100)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    for column in ("course_id", "video_id", "section_id"):
        op.create_index(f"ix_processing_jobs_{column}", "processing_jobs", [column])


def downgrade() -> None:
    op.drop_table("processing_jobs")
