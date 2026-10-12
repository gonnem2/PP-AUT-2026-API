from src.errors import AppError


class UserAlreadyExists(AppError):
    status_code = 409
    code = "USER_ALREADY_EXISTS"
    message = "User already exists"


class UserNotFound(AppError):
    status_code = 401
    code = "USER_NOT_FOUNT"
    message = "User not fount"


class ProfileNotFound(AppError):
    status_code = 404
    code = "PROFILE_NOT_FOUND"
    message = "Profile has not been filled in yet"


class InvalidCurrentPassword(AppError):
    status_code = 400
    code = "INVALID_CURRENT_PASSWORD"
    message = "Current password is incorrect"


class PasswordUnchanged(AppError):
    status_code = 400
    code = "PASSWORD_UNCHANGED"
    message = "New password must differ from current password"
