from pydantic import BaseModel, EmailStr, Field

from src.user.dto import UserDTO


class UserOut(BaseModel):
    id: int
    name: str
    email: str

    @classmethod
    def from_dto(cls, dto: UserDTO) -> "UserOut":
        return cls(
            id=dto.id,
            name=dto.name,
            email=dto.email,
        )


class UserIn(BaseModel):
    # Базовые данные для создания пользователя
    email: EmailStr
    name: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[a-zA-Z0-9_]+$",
    )
    surname: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[a-zA-Z0-9_]+$",
    )
    password: str = Field(min_length=1, max_length=100)
