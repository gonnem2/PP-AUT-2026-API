"""Библиотека профиля, строковые отчёты и надёжная очередь отправки в RabbitMQ."""

import sqlalchemy as sa
from alembic import op

revision = "d92f307be615"
down_revision = "b67123a45cde"
branch_labels = None
depends_on = None


def timestamps():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade():
    for column in [
        sa.Column("github_username", sa.String(39), nullable=True),
        sa.Column("declared_role", sa.Text(), nullable=True),
        sa.Column("started_at", sa.Date(), nullable=True),
        sa.Column("ended_at", sa.Date(), nullable=True),
        sa.Column("report_text", sa.Text(), nullable=True),
        sa.Column("summary_ru", sa.Text(), nullable=True),
        sa.Column("report_sha", sa.String(40), nullable=True),
        sa.Column("report_context_hash", sa.String(64), nullable=True),
        sa.Column("report_updated_at", sa.DateTime(timezone=True), nullable=True),
    ]:
        op.add_column("imported_projects", column)
    op.create_table(
        "github_report_state",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("library_revision", sa.Integer(), nullable=False),
        sa.Column("selected_repo_ids", sa.JSON(), nullable=False),
        sa.Column("latest_job_id", sa.String(36), nullable=True),
        sa.Column("report_job_id", sa.String(36), nullable=True),
        sa.Column("report_revision", sa.Integer(), nullable=True),
        sa.Column("report_text", sa.Text(), nullable=True),
        sa.Column("summary_ru", sa.Text(), nullable=True),
        *timestamps(),
    )
    op.create_table(
        "github_report_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("message_id", sa.String(36), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("input_revision", sa.Integer(), nullable=False),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=True),
        sa.Column("result_message_id", sa.String(36), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.String(36), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        *timestamps(),
        sa.UniqueConstraint(
            "user_id", "request_id", name="uq_github_report_job_request"
        ),
    )
    op.create_index("ix_github_report_jobs_user_id", "github_report_jobs", ["user_id"])
    op.create_index("ix_github_report_jobs_status", "github_report_jobs", ["status"])


def downgrade():
    op.drop_table("github_report_jobs")
    op.drop_table("github_report_state")
    for name in (
        "report_updated_at",
        "report_context_hash",
        "report_sha",
        "summary_ru",
        "report_text",
        "ended_at",
        "started_at",
        "declared_role",
        "github_username",
    ):
        op.drop_column("imported_projects", name)
