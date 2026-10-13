"""Persist resumes and imported public GitHub projects."""

import sqlalchemy as sa
from alembic import op

revision = "b67123a45cde"
down_revision = "4d2e89b167ab"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("users", "name", type_=sa.String(100), existing_type=sa.String(50))
    op.create_table(
        "resumes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_resumes_user_id", "resumes", ["user_id"])
    op.create_table(
        "imported_projects",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("repo_id", sa.String(100), primary_key=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("imported_projects")
    op.drop_index("ix_resumes_user_id", table_name="resumes")
    op.drop_table("resumes")
    # Retain the wider user name column to avoid truncating existing names.
