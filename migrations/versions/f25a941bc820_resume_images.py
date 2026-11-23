"""Галерея резюме, задания обработки и надёжная очистка S3."""

import sqlalchemy as sa
from alembic import op

revision = "f25a941bc820"
down_revision = "e4f6a9c01234"
branch_labels = None
depends_on = None


def timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def upgrade() -> None:
    op.add_column("resumes", sa.Column("avatar_image_id", sa.String(36)))
    op.add_column("resumes", sa.Column("avatar_request_id", sa.String(36)))
    op.create_table(
        "resume_images",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "resume_id",
            sa.String(36),
            sa.ForeignKey("resumes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(32), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("staging_key", sa.String(255)),
        sa.Column("full_key", sa.String(255)),
        sa.Column("preview_key", sa.String(255)),
        sa.Column("avatar_key", sa.String(255)),
        sa.Column("avatar_error", sa.Text()),
        *timestamps(),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'ready', 'failed')",
            name="ck_resume_images_status",
        ),
    )
    op.create_index("ix_resume_images_resume_id", "resume_images", ["resume_id"])
    op.create_table(
        "resume_image_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "image_id",
            sa.String(36),
            sa.ForeignKey("resume_images.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("make_avatar", sa.Boolean(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("dispatch_at", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        *timestamps(),
        sa.CheckConstraint(
            "kind IN ('upload', 'crop')", name="ck_resume_image_jobs_kind"
        ),
    )
    op.create_index("ix_resume_image_jobs_image_id", "resume_image_jobs", ["image_id"])
    op.create_index(
        "ix_resume_image_jobs_dispatch_at", "resume_image_jobs", ["dispatch_at"]
    )
    op.create_table(
        "resume_image_cleanup",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("staged", sa.Boolean(), nullable=False),
        sa.Column("keys", sa.JSON(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        *timestamps(),
    )
    op.create_index(
        "ix_resume_image_cleanup_available_at", "resume_image_cleanup", ["available_at"]
    )


def downgrade() -> None:
    op.drop_table("resume_image_cleanup")
    op.drop_table("resume_image_jobs")
    op.drop_table("resume_images")
    op.drop_column("resumes", "avatar_request_id")
    op.drop_column("resumes", "avatar_image_id")
