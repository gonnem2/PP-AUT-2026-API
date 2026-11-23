"""Храним структурированный ответ ML v2 рядом с выполненным заданием."""

import sqlalchemy as sa
from alembic import op

revision = "a1b293c71e05"
down_revision = "f25a941bc820"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("github_report_jobs", sa.Column("result_payload_v2", sa.JSON()))
    op.add_column("github_report_jobs", sa.Column("ml_status", sa.String(12)))
    op.add_column("github_report_jobs", sa.Column("ml_report_repo_sources", sa.JSON()))


def downgrade() -> None:
    op.drop_column("github_report_jobs", "ml_report_repo_sources")
    op.drop_column("github_report_jobs", "ml_status")
    op.drop_column("github_report_jobs", "result_payload_v2")
