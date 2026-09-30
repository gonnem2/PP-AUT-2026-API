from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

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
    name: Mapped[str] = mapped_column(String(50), unique=False)
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
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    full_name: Mapped[str] = mapped_column(String(100), unique=False, nullable=False)
    specialization: Mapped[str] = mapped_column(
        String(255), unique=False, nullable=False
    )
    about: Mapped[str] = mapped_column(Text(2000), unique=False, nullable=False)
    user: Mapped["User"] = relationship(
        "User",
        back_populates="profile",
    )
