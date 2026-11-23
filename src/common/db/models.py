from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter
from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from src.common.db.types import DataclassJSON
from src.projects.dto import ProjectSnapshot
from src.projects.ml_protocol import CoreResultV2, MLStatus, WireRequest
from src.settings import settings

time_func = lambda: datetime.now(tz=ZoneInfo(settings.timezone))


class Base(DeclarativeBase):
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=time_func,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=time_func,
        onupdate=time_func,
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=False)
    email: Mapped[str] = mapped_column(String(100), unique=True)
    hashed_password: Mapped[str] = mapped_column(String(255), unique=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    is_verified: Mapped[bool] = mapped_column(
        default=True, nullable=False
    )  # Верифицирован ли был Email

    profile: Mapped["Profile"] = relationship(
        "Profile",
        back_populates="user",
        cascade="all, delete-orphan",
    )


class Profile(Base):
    __tablename__ = "profiles"
    __table_args__ = (UniqueConstraint("user_id", name="uq_profiles_user_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    full_name: Mapped[str] = mapped_column(String(100), unique=False, nullable=False)
    specialization: Mapped[str] = mapped_column(
        String(255), unique=False, nullable=False
    )
    about: Mapped[str] = mapped_column(String(2000), unique=False, nullable=False)
    user: Mapped["User"] = relationship(
        "User",
        back_populates="profile",
    )


class Resume(Base):
    __tablename__ = "resumes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    # Ссылки меняются под блокировкой резюме вместе с привязанными изображениями.
    avatar_image_id: Mapped[str | None] = mapped_column(String(36))
    avatar_request_id: Mapped[str | None] = mapped_column(String(36))


class ResumeImage(Base):
    __tablename__ = "resume_images"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'processing', 'ready', 'failed')",
            name="ck_resume_images_status",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    resume_id: Mapped[str] = mapped_column(
        ForeignKey("resumes.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(32))
    size: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(String(12), default="queued")
    error: Mapped[str | None] = mapped_column(Text)
    staging_key: Mapped[str | None] = mapped_column(String(255))
    full_key: Mapped[str | None] = mapped_column(String(255))
    preview_key: Mapped[str | None] = mapped_column(String(255))
    avatar_key: Mapped[str | None] = mapped_column(String(255))
    avatar_error: Mapped[str | None] = mapped_column(Text)


class ResumeImageJob(Base):
    """Задания обработки и outbox используют одну строку."""

    __tablename__ = "resume_image_jobs"
    __table_args__ = (
        CheckConstraint("kind IN ('upload', 'crop')", name="ck_resume_image_jobs_kind"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    image_id: Mapped[str] = mapped_column(
        ForeignKey("resume_images.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(8))
    make_avatar: Mapped[bool] = mapped_column(default=False)
    attempts: Mapped[int] = mapped_column(default=0)
    dispatch_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResumeImageCleanup(Base):
    """Удаление объектов повторяется независимо от существования резюме."""

    __tablename__ = "resume_image_cleanup"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    staged: Mapped[bool] = mapped_column()
    keys: Mapped[list[str]] = mapped_column(JSON)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Vacancy(Base):
    __tablename__ = "vacancies"
    __table_args__ = (
        CheckConstraint(
            "status IN ('saved', 'sent', 'interview', 'offer', 'rejected')",
            name="ck_vacancies_status",
        ),
        CheckConstraint(
            "(resume_id IS NULL AND resume_title IS NULL AND resume_snapshot IS NULL) OR "
            "(resume_id IS NOT NULL AND resume_title IS NOT NULL AND resume_snapshot IS NOT NULL)",
            name="ck_vacancies_snapshot",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    company: Mapped[str] = mapped_column(String(200))
    position: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(String(2048), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="saved")
    next_step_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Это источник архивного снимка: удаление оригинала не удаляет сохранённую копию.
    resume_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    resume_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    resume_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)


class ImportedProject(Base):
    __tablename__ = "imported_projects"
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    repo_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)
    github_username: Mapped[str | None] = mapped_column(String(39), nullable=True)
    declared_role: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    ended_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    report_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_ru: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    report_context_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    report_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class GitHubReportState(Base):
    __tablename__ = "github_report_state"
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    library_revision: Mapped[int] = mapped_column(default=0)
    selected_repo_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    latest_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    report_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    report_revision: Mapped[int | None] = mapped_column(nullable=True)
    report_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_ru: Mapped[str | None] = mapped_column(Text, nullable=True)


class GitHubReportJob(Base):
    """Строка задания одновременно служит надёжной очередью отправки."""

    __tablename__ = "github_report_jobs"
    __table_args__ = (
        UniqueConstraint("user_id", "request_id", name="uq_github_report_job_request"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    request_id: Mapped[str] = mapped_column(String(36))
    message_id: Mapped[str] = mapped_column(String(36), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    input_revision: Mapped[int] = mapped_column()
    input_snapshot: Mapped[list[ProjectSnapshot]] = mapped_column(
        DataclassJSON(TypeAdapter(list[ProjectSnapshot]))
    )
    request_payload: Mapped[WireRequest | None] = mapped_column(
        DataclassJSON(TypeAdapter(WireRequest)), nullable=True
    )
    result_payload_v2: Mapped[CoreResultV2 | None] = mapped_column(
        DataclassJSON(TypeAdapter(CoreResultV2)), nullable=True, deferred=True
    )
    ml_status: Mapped[MLStatus | None] = mapped_column(String(12))
    ml_report_repo_sources: Mapped[dict[str, Literal["github", "cache"]] | None] = (
        mapped_column(JSON)
    )
    result_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    attempts: Mapped[int] = mapped_column(default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
