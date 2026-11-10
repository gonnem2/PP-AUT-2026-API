from fastapi import HTTPException

from src.errors import AppError


class ProjectNotFound(AppError):
    status_code = 404
    code = "GITHUB_PROJECT_NOT_FOUND"
    message = "Проект не найден в вашей библиотеке"


class ReportJobNotFound(AppError):
    status_code = 404
    code = "GITHUB_REPORT_JOB_NOT_FOUND"
    message = "Задание не найдено"


class ReportConflict(AppError):
    status_code = 409
    code = "GITHUB_REPORT_CONFLICT"
    message = "Данные изменились; обновите запрос"


class InvalidCoreResult(ValueError):
    """Невалидный ответ отправляется в DLQ без применения результата."""


class EmptyGitHubRepository(HTTPException):
    """Пустота подтверждена ответом GitHub на запрос коммита, а не размером репозитория."""

    def __init__(self, full_name: str) -> None:
        super().__init__(
            422,
            f"В репозитории {full_name} нет коммитов. Уберите его из выбора или добавьте первый коммит.",
        )
