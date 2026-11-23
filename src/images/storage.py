"""S3 хранит исходники и WebP в разных приватных бакетах."""

import asyncio
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

from boto3.session import Session
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from src.settings import ImageSettings, settings


class StorageUnavailable(RuntimeError):
    pass


class ObjectMissing(StorageUnavailable):
    pass


@dataclass(frozen=True)
class SignedImage:
    url: str
    expiresAt: datetime


class ImageStorage:
    def __init__(self, config: ImageSettings) -> None:
        if not config.image_s3_endpoint or not config.image_s3_secret_key:
            raise StorageUnavailable("Хранилище изображений не настроено")
        self.config = config
        session = Session(
            aws_access_key_id=config.image_s3_access_key,
            aws_secret_access_key=config.image_s3_secret_key.get_secret_value(),
            region_name=config.image_s3_region,
        )
        options = Config(
            connect_timeout=3,
            read_timeout=10,
            retries={"total_max_attempts": 2},
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        )
        self.client: Any = session.client(
            "s3", endpoint_url=config.image_s3_endpoint, config=options
        )
        public_endpoint = config.image_s3_public_endpoint or config.image_s3_endpoint
        self.signer: Any = (
            self.client
            if public_endpoint == config.image_s3_endpoint
            else session.client("s3", endpoint_url=public_endpoint, config=options)
        )

    def bucket(self, staged: bool) -> str:
        return (
            self.config.image_s3_staging_bucket
            if staged
            else self.config.image_s3_bucket
        )

    async def put(
        self, key: str, body: bytes, content_type: str, *, staged: bool = False
    ) -> None:
        try:
            await asyncio.to_thread(
                self.client.put_object,
                Bucket=self.bucket(staged),
                Key=key,
                Body=body,
                ContentType=content_type,
                CacheControl="private, no-store",
            )
        except (BotoCoreError, ClientError, OSError) as error:
            raise StorageUnavailable("Не удалось записать изображение") from error

    async def get(self, key: str, limit: int, *, staged: bool = False) -> bytes:
        def read() -> bytes:
            result = self.client.get_object(Bucket=self.bucket(staged), Key=key)
            with closing(result["Body"]) as stream:
                return stream.read(limit + 1)

        try:
            data = await asyncio.to_thread(read)
        except ClientError as error:
            if error.response["Error"]["Code"] in {"NoSuchKey", "404"}:
                raise ObjectMissing("Файл в хранилище не найден") from error
            raise StorageUnavailable("Не удалось прочитать изображение") from error
        except (BotoCoreError, OSError) as error:
            raise StorageUnavailable("Не удалось прочитать изображение") from error
        if len(data) > limit:
            raise StorageUnavailable("Файл превышает ожидаемый размер")
        return data

    async def delete(self, keys: list[str], *, staged: bool) -> None:
        try:
            for key in keys:
                await asyncio.to_thread(
                    self.client.delete_object, Bucket=self.bucket(staged), Key=key
                )
        except (BotoCoreError, ClientError, OSError) as error:
            raise StorageUnavailable("Не удалось удалить изображение") from error

    def sign(self, key: str) -> SignedImage:
        ttl = self.config.image_url_seconds
        try:
            url = self.signer.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket(False), "Key": key},
                ExpiresIn=ttl,
            )
        except (BotoCoreError, ClientError, ValueError) as error:
            raise StorageUnavailable("Не удалось подписать ссылку") from error
        return SignedImage(url, datetime.now(UTC) + timedelta(seconds=ttl))


@lru_cache(maxsize=1)
def get_image_storage() -> ImageStorage:
    return ImageStorage(settings.images)
