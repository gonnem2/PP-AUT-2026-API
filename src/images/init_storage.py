"""Создаёт приватные бакеты SeaweedFS и задаёт срок хранения исходников."""

import logging
import time

from botocore.exceptions import BotoCoreError, ClientError

from src.images.storage import get_image_storage

logger = logging.getLogger(__name__)


def configure() -> None:
    storage = get_image_storage()
    for staged in (False, True):
        bucket = storage.bucket(staged)
        try:
            storage.client.head_bucket(Bucket=bucket)
        except ClientError as error:
            if error.response["Error"]["Code"] not in {
                "404",
                "NoSuchBucket",
                "NotFound",
            }:
                raise
            storage.client.create_bucket(Bucket=bucket)
    storage.client.put_bucket_lifecycle_configuration(
        Bucket=storage.bucket(True),
        LifecycleConfiguration={
            "Rules": [
                {
                    "ID": "expire-original-uploads",
                    "Status": "Enabled",
                    "Filter": {"Prefix": ""},
                    "Expiration": {"Days": 7},
                }
            ]
        },
    )


def main() -> None:
    for attempt in range(20):
        try:
            configure()
        except (BotoCoreError, ClientError, OSError) as error:
            if attempt == 19:
                logger.error("Не удалось подготовить бакеты (%s)", type(error).__name__)
                raise SystemExit(1) from None
            time.sleep(2)
        else:
            return


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
