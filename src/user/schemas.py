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


class ProfileIn(BaseModel):
    model_config = {"extra": "forbid", "str_strip_whitespace": True}

    full_name: str = Field(min_length=1, max_length=100)
    specialization: str = Field(min_length=1, max_length=255)
    about: str = Field(max_length=2000)


class ProfilePatch(BaseModel):
    model_config = {"extra": "forbid", "str_strip_whitespace": True}

    full_name: str = Field(default=None, min_length=1, max_length=100)
    specialization: str = Field(default=None, min_length=1, max_length=255)
    about: str = Field(default=None, max_length=2000)


class ProfileOut(ProfileIn):
    model_config = {"from_attributes": True}

    id: int
    user_id: int


class PasswordChange(BaseModel):
    model_config = {"extra": "forbid"}

    current_password: str = Field(min_length=1, max_length=100)
    new_password: str = Field(min_length=8, max_length=100)
