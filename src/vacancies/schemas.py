"""HTTP-контракт откликов; имена полей совместимы с формой фронтенда."""

from dataclasses import field
from datetime import date, datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import (
    ConfigDict,
    Field,
    HttpUrl,
    TypeAdapter,
    field_validator,
    model_validator,
)
from pydantic.dataclasses import dataclass
from pydantic_core import ArgsKwargs

from src.resume.schemas import Content

VacancyStatus = Literal["saved", "sent", "interview", "offer", "rejected"]
Name = Annotated[str, Field(min_length=1, max_length=200)]
Description = Annotated[str, Field(max_length=50000)]
Notes = Annotated[str, Field(max_length=20000)]
Link = Annotated[str, Field(max_length=2048)]


@dataclass(config=ConfigDict(extra="forbid"), kw_only=True)
class VacancyFields:
    """Общие проверки не подменяют неизвестные данные тестовыми значениями."""

    @field_validator("company", "position", check_fields=False, mode="before")
    @classmethod
    def trim_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("url", check_fields=False)
    @classmethod
    def public_link(cls, value: str | None) -> str | None:
        if value:
            value = str(TypeAdapter(HttpUrl).validate_python(value.strip()))
        return value

    @field_validator("nextStepDate", check_fields=False)
    @classmethod
    def supported_date(cls, value: date | None) -> date | None:
        if value and not 1970 <= value.year <= 2099:
            raise ValueError("Год следующего действия должен быть от 1970 до 2099")
        return value


@dataclass(kw_only=True)
class VacancyCreate(VacancyFields):
    company: Name
    position: Name
    url: Link = ""
    description: Description = ""
    notes: Notes = ""
    status: VacancyStatus = "saved"
    nextStepDate: date | None = None
    resumeId: UUID | None = None


@dataclass(kw_only=True)
class VacancyPatch(VacancyFields):
    company: Name | None = None
    position: Name | None = None
    url: Link | None = None
    description: Description | None = None
    notes: Notes | None = None
    status: VacancyStatus | None = None
    nextStepDate: date | None = None
    resumeId: UUID | None = None
    _provided_fields: frozenset[str] = field(
        init=False, repr=False, default_factory=frozenset
    )

    @field_validator("company", "position", "url", "description", "notes", "status")
    @classmethod
    def cannot_clear_required(cls, value: Any) -> Any:
        # Валидатор вызывается для явного null, но не для пропущенного поля.
        if value is None:
            raise ValueError("Это поле не может быть null")
        return value

    @model_validator(mode="wrap")
    @classmethod
    def remember_fields(cls, data: Any, handler: Any) -> Self:
        result = handler(data)
        # Словарь существует только на входной границе Pydantic.
        if isinstance(data, dict):
            result._provided_fields = frozenset(data)
        elif isinstance(data, ArgsKwargs):
            result._provided_fields = frozenset(data.kwargs or {})
        return result

    @property
    def provided_fields(self) -> frozenset[str]:
        return self._provided_fields


@dataclass(kw_only=True)
class VacancyView:
    id: str
    company: str
    position: str
    url: str
    description: str
    notes: str
    status: VacancyStatus
    nextStepDate: date | None
    resumeId: str | None
    resumeTitle: str | None
    createdAt: datetime
    updatedAt: datetime


@dataclass(kw_only=True)
class ResumeSnapshotView:
    id: str
    title: str
    content: Content
