from src.errors import AppError


class InvalidToken(AppError):
    status_code = 401
    code = "INVALID_TOKEN"
    message = "Invalid or expired token"
