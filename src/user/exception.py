from src.errors import AppError


class UserAlreadyExists(AppError):
    code = "USER_ALREADY_EXISTS"
    message = "User already exists"
