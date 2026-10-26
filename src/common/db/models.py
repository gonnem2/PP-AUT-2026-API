from datetime import date, datetime
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter
from sqlalchemy import JSON, Date, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from src.common.db.types import DataclassJSON
from src.projects.dto import ProjectSnapshot
from src.projects.schemas import CoreRequest
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
    request_payload: Mapped[CoreRequest | None] = mapped_column(
        DataclassJSON(TypeAdapter(CoreRequest)), nullable=True
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
