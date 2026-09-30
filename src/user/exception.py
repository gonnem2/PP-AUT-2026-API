from src.errors import AppError


class UserAlreadyExists(AppError):
    status_code = 409
    code = "USER_ALREADY_EXISTS"
    message = "User already exists"


class UserNotFound(AppError):
    status_code = 401
    code = "USER_NOT_FOUNT"
    message = "User not fount"
