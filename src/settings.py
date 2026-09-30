from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict

env_file = ".env"


class AppBaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=env_file,
        extra="ignore",
    )


class DBSettings(AppBaseSettings):
    db_port: int = Field(..., alias="APP_DB_PORT")
    db_host: str = Field(..., alias="APP_DB_HOST")
    db_user: str = Field(..., alias="APP_DB_USER")
    db_password: str = Field(..., alias="APP_DB_PASSWORD")
    db_name: str = Field(..., alias="APP_DB_NAME")

    @property
    def dsn(self) -> str:
        return str(
            PostgresDsn.build(
                scheme="postgresql+asyncpg",
                username=self.db_user,
                password=self.db_password,
                host=self.db_host,
                port=self.db_port,
                path=self.db_name,
            )
        )


class SecuritySettings(AppBaseSettings):
    access_token_expire_minutes: int = Field(
        15,
        description="Время действия access токена",
        alias="ACCESS_TOKEN_EXPIRE_MINUTES",
    )
    refresh_token_expire_minutes: int = Field(
        3600,
        description="Время действия refresh токенов",
        alias="REFRESH_TOKEN_EXPIRE_MINUTES",
    )

    secret_key: str = Field(
        "super=secret=key_my",
        description="Секретный ключ",
        alias="SECRET_KEY",
    )
    algorithm: str = Field("HS256", alias="ALGORITHM")


class AppSettings(AppBaseSettings):
    redis_dsn: str = Field("redis://localhost:6379", alias="REDIS_DSN")

    timezone: str = Field("UTC", alias="APP_TIMEZONE")

    db_settings: DBSettings = Field(default_factory=DBSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)


settings = AppSettings()
