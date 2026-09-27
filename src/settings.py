import datetime

from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict

env_file = ".env"


class AppBaseSettings(BaseSettings):
    config: SettingsConfigDict = SettingsConfigDict(env_file=env_file)


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


class AppSettings(AppBaseSettings):
    timezone: datetime.timezone = Field(datetime.UTC, alias="APP_TIMEZONE")
    db_settings: DBSettings = Field(default_factory=DBSettings)


settings = AppSettings()
