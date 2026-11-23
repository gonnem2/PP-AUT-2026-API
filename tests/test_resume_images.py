"""HTTP, outbox, Pillow и PDF с подменёнными RabbitMQ и S3."""

import unittest
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from io import BytesIO
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from PIL import Image
from pillow_heif import from_pillow
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.auth.dependencies import get_current_user
from src.common.db.models import Base, Resume, ResumeImageJob, User
from src.common.db.session import get_async_session
from src.images import router as image_routes
from src.images.processing import MAX_INPUT_BYTES, process_upload, sniff_content_type
from src.images.storage import ImageStorage, SignedImage
from src.images.worker import ImageWorker
from src.main import app
from src.resume import router as resume_routes
from src.settings import ImageSettings
from src.user.dto import UserDTO


def photo() -> bytes:
    output = BytesIO()
    with Image.new("RGBA", (640, 480), (255, 30, 80, 128)) as image:
        image.save(output, "PNG")
    return output.getvalue()


def heic_photo() -> bytes:
    output = BytesIO()
    with (
        Image.new("RGB", (48, 32), "blue") as other,
        Image.new("RGB", (40, 24), "red") as primary,
    ):
        heif = from_pillow(other)
        heif.add_from_pillow(primary)
        heif.save(output, primary_index=1)
    return output.getvalue()


class MemoryStorage:
    def __init__(self) -> None:
        self.objects: dict[tuple[bool, str], bytes] = {}

    async def put(
        self, key: str, body: bytes, _type: str, *, staged: bool = False
    ) -> None:
        self.objects[staged, key] = body

    async def get(self, key: str, _limit: int, *, staged: bool = False) -> bytes:
        return self.objects[staged, key]

    async def delete(self, keys: list[str], *, staged: bool) -> None:
        for key in keys:
            self.objects.pop((staged, key), None)

    def sign(self, key: str) -> SignedImage:
        return SignedImage(f"https://images.example/{key}", datetime.now(UTC))


class PillowTests(unittest.TestCase):
    def test_variants_keep_alpha(self) -> None:
        variants = process_upload(photo(), make_avatar=True)
        assert variants.avatar is not None
        for data, size in (
            (variants.full, (640, 480)),
            (variants.preview, (400, 300)),
            (variants.avatar, (480, 480)),
        ):
            with Image.open(BytesIO(data)) as image:
                self.assertEqual(
                    (image.format, image.size, image.mode), ("WEBP", size, "RGBA")
                )

    def test_animated_webp_and_apng_keep_first_available_frame(self) -> None:
        for format_name, options in (
            ("WEBP", {"lossless": True}),
            ("PNG", {"default_image": True}),
        ):
            with (
                self.subTest(format_name=format_name),
                Image.new("RGB", (48, 32), "red") as first,
                Image.new("RGB", (48, 32), "blue") as second,
                Image.new("RGB", (48, 32), "green") as third,
            ):
                output = BytesIO()
                first.save(
                    output,
                    format_name,
                    save_all=True,
                    append_images=[second, third],
                    duration=100,
                    **options,
                )
                variants = process_upload(output.getvalue(), make_avatar=True)
                assert variants.avatar is not None
                for data in (variants.full, variants.preview, variants.avatar):
                    with Image.open(BytesIO(data)) as result:
                        self.assertEqual(result.n_frames, 1)
                        red, _, blue = result.getpixel((8, 8))
                        self.assertGreater(red, blue)

    def test_heic_uses_its_primary_photo(self) -> None:
        data = heic_photo()
        self.assertEqual(sniff_content_type(data), "image/heic")
        with Image.open(BytesIO(process_upload(data, make_avatar=False).full)) as image:
            self.assertEqual(image.size, (40, 24))
            red, _, blue = image.getpixel((8, 8))
            self.assertGreater(red, blue)

    def test_presign_uses_browser_endpoint(self) -> None:
        storage = ImageStorage(
            ImageSettings(
                image_s3_endpoint="http://seaweedfs:8333",
                image_s3_public_endpoint="http://localhost:8333",
                image_s3_access_key="test",
                image_s3_secret_key="test-only-secret",
            )
        )
        url = urlsplit(storage.sign("private/test.webp").url)
        self.assertEqual(url.netloc, "localhost:8333")
        self.assertEqual(url.path, "/resumehub-images/private/test.webp")
        self.assertEqual(parse_qs(url.query)["X-Amz-Expires"], ["900"])


class ResumeImagesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.addAsyncCleanup(self.engine.dispose)

        @event.listens_for(self.engine.sync_engine, "connect")
        def foreign_keys(connection, _) -> None:
            connection.execute("PRAGMA foreign_keys=ON")

        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.resume_id = str(uuid4())
        async with self.sessions.begin() as db:
            db.add(
                User(
                    id=1,
                    name="Owner",
                    email="owner@example.com",
                    hashed_password="unused",
                )
            )
            await db.flush()
            db.add(Resume(id=self.resume_id, user_id=1, title="Резюме", content={}))

        self.storage = MemoryStorage()
        for module in (image_routes, resume_routes):
            mocked = patch.object(
                module, "get_image_storage", return_value=self.storage
            )
            mocked.start()
            self.addCleanup(mocked.stop)

        async def database() -> AsyncIterator[AsyncSession]:
            async with self.sessions() as db:
                yield db

        self.user_id = 1

        def user() -> UserDTO:
            return UserDTO(
                self.user_id, "Owner", "owner@example.com", "unused", True, True
            )

        app.dependency_overrides[get_async_session] = database
        app.dependency_overrides[get_current_user] = user
        self.addCleanup(app.dependency_overrides.clear)
        self.client = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        )
        self.addAsyncCleanup(self.client.aclose)
        self.base = f"/api/v1/resumes/{self.resume_id}"
        self.worker = ImageWorker(self.storage, self.sessions)

    async def upload(self, route: str = "images", data: bytes | None = None) -> str:
        response = await self.client.post(
            f"{self.base}/{route}",
            files={
                "file": ("test.png", photo() if data is None else data, "image/png")
            },
        )
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["status"], "queued")
        return response.json()["imageId"]

    async def gallery(self) -> dict[str, dict]:
        response = await self.client.get(f"{self.base}/images")
        self.assertEqual(response.status_code, 200, response.text)
        return {item["id"]: item for item in response.json()}

    async def dispatch(self) -> AsyncMock:
        broker = AsyncMock()
        self.assertTrue(await self.worker.dispatch_one(broker))
        message = AsyncMock()
        message.body = broker.publish.await_args.args[0].body
        return message

    async def test_queued_image_crop_pdf_and_delete(self) -> None:
        image_id = await self.upload()
        self.assertEqual((await self.gallery())[image_id]["status"], "queued")
        url = f"{self.base}/images/{image_id}/url"
        self.assertEqual((await self.client.get(url)).status_code, 409)
        delivery = await self.dispatch()
        await self.worker.consume(delivery)
        await self.worker.consume(delivery)
        self.assertEqual(delivery.ack.await_count, 2)
        self.assertEqual((await self.gallery())[image_id]["status"], "ready")
        self.assertTrue(
            (await self.client.get(url)).json()["url"].endswith("/preview.webp")
        )
        result = await self.client.put(
            f"{self.base}/avatar", json={"imageId": image_id}
        )
        self.assertEqual(result.status_code, 202)
        self.assertEqual((await self.gallery())[image_id]["avatarStatus"], "queued")

        await self.worker.consume(await self.dispatch())
        self.assertTrue((await self.gallery())[image_id]["isAvatar"])
        avatar = await self.client.get(url, params={"variant": "avatar"})
        self.assertTrue(avatar.json()["url"].endswith("/avatar.webp"))
        pdf = await self.client.get(f"{self.base}/pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertIn(b"/Subtype /Image", pdf.content)

        self.assertEqual(
            (await self.client.delete(f"{self.base}/images/{image_id}")).status_code,
            204,
        )
        self.assertEqual(await self.gallery(), {})
        while await self.worker.cleanup_one():
            pass
        self.assertEqual(self.storage.objects, {})

    async def test_errors_and_stale_avatar_upload_keep_newer_selection(self) -> None:
        original = await self.upload("avatar")
        await self.worker.consume(await self.dispatch())
        invalid = await self.upload("avatar", b"\x89PNG\r\n\x1a\ninvalid")
        await self.worker.consume(await self.dispatch())
        gallery = await self.gallery()
        self.assertTrue(gallery[original]["isAvatar"])
        self.assertEqual(gallery[invalid]["status"], "failed")
        self.assertTrue(gallery[invalid]["avatarError"])

        late = await self.upload("avatar")
        work = await self.worker.claim(late)
        assert work is not None
        self.assertEqual((await self.gallery())[late]["avatarStatus"], "processing")
        response = await self.client.put(f"{self.base}/avatar", json={"imageId": None})
        self.assertEqual(response.status_code, 204)
        await self.worker.process(work)
        await self.worker.finish(work)
        gallery = await self.gallery()
        self.assertFalse(any(item["isAvatar"] for item in gallery.values()))
        self.assertEqual(gallery[late]["status"], "ready")

    async def test_outbox_and_processing_lease_recover(self) -> None:
        image_id = await self.upload()
        broker = AsyncMock()
        broker.publish.side_effect = RuntimeError("broker unavailable")
        self.assertTrue(await self.worker.dispatch_one(broker))
        first_message = broker.publish.await_args.args[0]
        async with self.sessions.begin() as db:
            job = await db.scalar(select(ResumeImageJob))
            assert job is not None
            self.assertEqual(job.id, image_id)
            job.dispatch_at = datetime.now(UTC) - timedelta(seconds=1)
        delivery = await self.dispatch()
        self.assertEqual(delivery.body, first_message.body)

        stale = await self.worker.claim(image_id)
        assert stale is not None
        self.assertIsNone(await self.worker.claim(image_id))
        async with self.sessions.begin() as db:
            await db.execute(
                update(ResumeImageJob).values(
                    lease_until=datetime.now(UTC) - timedelta(seconds=1)
                )
            )
        await self.worker.consume(delivery)
        await self.worker.finish(stale, "Старый ответ")
        self.assertEqual((await self.gallery())[image_id]["status"], "ready")

    async def test_owner_and_upload_limits(self) -> None:
        self.user_id = 2
        denied = await self.client.post(
            f"{self.base}/images", files={"file": ("test.png", photo())}
        )
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(self.storage.objects, {})
        self.user_id = 1
        invalid = await self.client.post(
            f"{self.base}/images", files={"file": ("test.png", b"fake")}
        )
        self.assertEqual(invalid.status_code, 415)
        large = await self.client.post(
            f"{self.base}/images",
            files={"file": ("test.png", b"z" * (MAX_INPUT_BYTES + 1))},
        )
        self.assertEqual(large.status_code, 413)
