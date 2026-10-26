"""Адаптер dataclass DTO к существующим JSON-колонкам без изменения схемы БД."""

from typing import cast

from pydantic import JsonValue, TypeAdapter
from sqlalchemy import JSON, Dialect
from sqlalchemy.types import TypeDecorator


class DataclassJSON[T](TypeDecorator[T]):
    """БД хранит JSON, а ORM читает и записывает типизированные объекты."""

    impl = JSON
    # TypeAdapter не участвует в ключе кеша SQLAlchemy: для этого типа кеш отключён.
    cache_ok = False

    def __init__(self, adapter: TypeAdapter[T]) -> None:
        super().__init__()
        self.adapter = adapter

    def process_bind_param(self, value: T | None, dialect: Dialect) -> JsonValue:
        return (
            None
            if value is None
            else cast(JsonValue, self.adapter.dump_python(value, mode="json"))
        )

    def process_result_value(self, value: JsonValue, dialect: Dialect) -> T | None:
        return None if value is None else self.adapter.validate_python(value)
