from typing import Literal

from pydantic import Field, PostgresDsn, SecretStr, model_validator
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


class BrokerSettings(AppBaseSettings):
    rabbitmq_url: str = "amqp://resumehub:resumehub@localhost:5672/"
    ml_exchange: str = "resumehub.ml"
    ml_request_queue: str = "resumehub.ml.requests"
    ml_result_queue: str = "resumehub.ml.results"
    ml_dead_letter_queue: str = "resumehub.ml.invalid-results"
    ml_request_key: str = "github.report.requested"
    ml_completed_key: str = "github.report.completed"
    ml_failed_key: str = "github.report.failed"
    rabbitmq_prefetch_count: int = Field(1, ge=1)
    report_message_bytes: int = Field(4_000_000, ge=1024)


class ReportWorkerSettings(BrokerSettings):
    report_protocol_version: int = Field(1, ge=1, le=2)
    report_prepare_timeout: int = Field(120, ge=1, le=120)
    report_lease_seconds: int = Field(180, ge=180)
    report_max_attempts: int = Field(5, ge=1, le=20)
    report_result_timeout: int = Field(1800, ge=60)


class ImageSettings(AppBaseSettings):
    image_s3_endpoint: str | None = None
    image_s3_public_endpoint: str | None = None
    image_s3_access_key: str | None = None
    image_s3_secret_key: SecretStr | None = None
    image_s3_region: str = "us-east-1"
    image_s3_bucket: str = "resumehub-images"
    image_s3_staging_bucket: str = "resumehub-image-uploads"
    image_url_seconds: int = Field(900, ge=30, le=3600)
    image_queue: str = "resumehub.images"

    @model_validator(mode="after")
    def validate_storage(self) -> "ImageSettings":
        if self.image_s3_endpoint and (
            not self.image_s3_access_key
            or not self.image_s3_secret_key
            or not self.image_s3_secret_key.get_secret_value()
            or self.image_s3_bucket == self.image_s3_staging_bucket
        ):
            raise ValueError("Для S3 изображений нужны ключи и два разных бакета")
        return self


class AppSettings(AppBaseSettings):
    ml_core_mode: Literal["demo", "external"] = "external"

    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"],
        alias="CORS_ORIGINS",
    )
    github_token: str | None = Field(default=None, alias="GITHUB_TOKEN")

    redis_dsn: str = Field("redis://localhost:6379", alias="REDIS_DSN")

    timezone: str = Field("UTC", alias="APP_TIMEZONE")

    db_settings: DBSettings = Field(default_factory=DBSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    reports: ReportWorkerSettings = Field(default_factory=ReportWorkerSettings)
    images: ImageSettings = Field(default_factory=ImageSettings)


settings = AppSettings()
