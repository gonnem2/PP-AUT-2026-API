"""Вакансии и отклики с неизменяемыми снимками резюме."""

import sqlalchemy as sa
from alembic import op

revision = "e4f6a9c01234"
down_revision = "d92f307be615"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vacancies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("company", sa.String(200), nullable=False),
        sa.Column("position", sa.String(200), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("next_step_date", sa.Date(), nullable=True),
        sa.Column("resume_id", sa.String(36), nullable=True),
        sa.Column("resume_title", sa.String(200), nullable=True),
        sa.Column("resume_snapshot", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('saved', 'sent', 'interview', 'offer', 'rejected')",
            name="ck_vacancies_status",
        ),
        sa.CheckConstraint(
            "(resume_id IS NULL AND resume_title IS NULL AND resume_snapshot IS NULL) OR "
            "(resume_id IS NOT NULL AND resume_title IS NOT NULL AND resume_snapshot IS NOT NULL)",
            name="ck_vacancies_snapshot",
        ),
    )
    op.create_index("ix_vacancies_user_id", "vacancies", ["user_id"])


def downgrade() -> None:
    op.drop_table("vacancies")
