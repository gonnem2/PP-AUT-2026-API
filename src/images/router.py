"""HTTP-контракт галереи резюме и фонового выбора аватарки."""

from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.formparsers import MultiPartException
from starlette.types import Message

from src.auth.dependencies import get_current_user
from src.common.db.models import ResumeImage, ResumeImageJob
from src.common.db.session import get_async_session
from src.images import repo
from src.images.processing import MAX_INPUT_BYTES, InvalidImage, sniff_content_type
from src.images.storage import SignedImage, StorageUnavailable, get_image_storage
from src.user.dto import UserDTO


class BoundedUploadRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        inner = super().get_route_handler()
        if "POST" not in self.methods:
            return inner

        async def handler(request: Request) -> Response:
            # Ограничиваем и файл, и сетевое multipart-тело с запасом на заголовки.
            limit = MAX_INPUT_BYTES + 64 * 1024
            declared = request.headers.get("content-length", "")
            if declared.isdecimal() and (len(declared) > 12 or int(declared) > limit):
                raise HTTPException(413, "Изображение больше 5 МБ")
            received = 0

            async def receive() -> Message:
                nonlocal received
                message = await request.receive()
                received += len(message.get("body", b""))
                if received > limit:
                    # MultiPartException закрывает временные файлы Starlette.
                    raise MultiPartException("Изображение больше 5 МБ")
                return message

            try:
                return await inner(Request(request.scope, receive))
            except HTTPException as error:
                if received > limit:
                    raise HTTPException(413, "Изображение больше 5 МБ") from error
                raise

        return handler


router = APIRouter(
    prefix="/api/v1/resumes/{resume_id}",
    tags=["resume-images"],
    route_class=BoundedUploadRoute,
)
DB = Annotated[AsyncSession, Depends(get_async_session)]
User = Annotated[UserDTO, Depends(get_current_user)]
Upload = Annotated[UploadFile, File()]
Variant = Literal["preview", "full", "avatar"]


@dataclass(frozen=True)
class AcceptedImage:
    imageId: str
    status: Literal["queued"] = "queued"


@dataclass(frozen=True)
class ImageView:
    id: str
    filename: str
    contentType: str
    size: int
    createdAt: datetime
    isAvatar: bool
    status: str
    error: str | None
    avatarStatus: Literal["queued", "processing", "ready", "failed"] | None
    avatarError: str | None


class AvatarSelection(BaseModel):
    imageId: UUID | None


@router.get("/images")
async def images(
    resume_id: UUID, response: Response, db: DB, user: User
) -> list[ImageView]:
    avatar_id, pending, rows = await repo.gallery_status(db, user.id, resume_id)
    result: list[ImageView] = []
    for row in rows:
        avatar_status = None
        if pending and pending.image_id == row.id:
            avatar_status = "processing" if pending.lease_token else "queued"
        elif row.avatar_key:
            avatar_status = "ready"
        elif row.avatar_error:
            avatar_status = "failed"
        result.append(
            ImageView(
                id=row.id,
                filename=row.filename,
                contentType=row.content_type,
                size=row.size,
                createdAt=row.created_at,
                isAvatar=row.id == avatar_id,
                status=row.status,
                error=row.error,
                avatarStatus=avatar_status,
                avatarError=row.avatar_error,
            )
        )
    response.headers["Cache-Control"] = "no-store"
    return result


async def stage_upload(
    resume_id: UUID,
    db: AsyncSession,
    user_id: int,
    file: UploadFile,
    *,
    make_avatar: bool,
) -> AcceptedImage:
    await repo.owned_resume(db, user_id, resume_id)
    await db.rollback()
    data = await file.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise HTTPException(413, "Изображение больше 5 МБ")
    try:
        content_type = sniff_content_type(data)
    except InvalidImage as error:
        raise HTTPException(415, str(error)) from error
    image_id = str(uuid4())
    key = f"{resume_id}/{image_id}"
    filename = (file.filename or "photo").replace("\\", "/").rsplit("/", 1)[-1]
    filename = (
        "".join(char for char in filename if char.isprintable()).strip()[:255]
        or "photo"
    )
    try:
        await get_image_storage().put(key, data, content_type, staged=True)
    except StorageUnavailable as error:
        raise HTTPException(503, "Хранилище изображений недоступно") from error

    # Между S3 и БД общей транзакции нет. Если commit не состоится, исходник удалит TTL.
    resume = await repo.owned_resume(db, user_id, resume_id, lock=True)
    row = ResumeImage(
        id=image_id,
        resume_id=resume.id,
        filename=filename,
        content_type=content_type,
        size=len(data),
        staging_key=key,
    )
    db.add(row)
    await db.flush()
    job = ResumeImageJob(
        id=image_id,
        image_id=image_id,
        kind="upload",
        make_avatar=make_avatar,
        dispatch_at=datetime.now(UTC),
    )
    db.add(job)
    if make_avatar:
        resume.avatar_request_id = job.id
    await db.commit()
    return AcceptedImage(image_id)


@router.post("/images", status_code=202)
async def upload_image(
    resume_id: UUID, file: Upload, db: DB, user: User
) -> AcceptedImage:
    return await stage_upload(resume_id, db, user.id, file, make_avatar=False)


@router.post("/avatar", status_code=202)
async def upload_avatar(
    resume_id: UUID, file: Upload, db: DB, user: User
) -> AcceptedImage:
    return await stage_upload(resume_id, db, user.id, file, make_avatar=True)


@router.get("/images/{image_id}/url")
async def image_url(
    resume_id: UUID,
    image_id: UUID,
    response: Response,
    db: DB,
    user: User,
    variant: Variant = "preview",
) -> SignedImage:
    resume = await repo.owned_resume(db, user.id, resume_id)
    row = await repo.image(db, resume.id, image_id)
    key = {"full": row.full_key, "preview": row.preview_key, "avatar": row.avatar_key}[
        variant
    ]
    if row.status != "ready":
        raise HTTPException(409, row.error or "Изображение ещё обрабатывается")
    if variant == "avatar" and resume.avatar_image_id != row.id:
        raise HTTPException(404, "Аватар не найден")
    if key is None:
        raise HTTPException(409, "Вариант изображения ещё не готов")
    try:
        signed = get_image_storage().sign(key)
    except StorageUnavailable as error:
        raise HTTPException(503, "Не удалось выдать ссылку на изображение") from error
    response.headers["Cache-Control"] = "no-store"
    return signed


@router.put("/avatar", status_code=204, responses={202: {"model": AcceptedImage}})
async def select_avatar(
    resume_id: UUID, data: AvatarSelection, db: DB, user: User
) -> Response:
    resume = await repo.owned_resume(db, user.id, resume_id, lock=True)
    if data.imageId is None:
        resume.avatar_image_id = None
        resume.avatar_request_id = None
        await db.commit()
        return Response(status_code=204)
    row = await repo.image(db, resume.id, data.imageId)
    if row.status != "ready":
        raise HTTPException(409, row.error or "Изображение ещё обрабатывается")
    if row.avatar_key:
        resume.avatar_image_id = row.id
        resume.avatar_request_id = None
        await db.commit()
        return Response(status_code=204)
    pending = await repo.pending_avatar(db, resume)
    if pending is None or pending.image_id != row.id:
        job_id = str(uuid4())
        db.add(
            ResumeImageJob(
                id=job_id, image_id=row.id, kind="crop", dispatch_at=datetime.now(UTC)
            )
        )
        resume.avatar_request_id = job_id
        row.avatar_error = None
        await db.commit()
    return JSONResponse(asdict(AcceptedImage(row.id)), status_code=202)


@router.delete("/images/{image_id}", status_code=204)
async def delete_image(resume_id: UUID, image_id: UUID, db: DB, user: User) -> Response:
    resume = await repo.owned_resume(db, user.id, resume_id, lock=True)
    row = await repo.image(db, resume.id, image_id)
    if resume.avatar_image_id == row.id:
        resume.avatar_image_id = None
    pending = await repo.pending_avatar(db, resume)
    if pending and pending.image_id == row.id:
        resume.avatar_request_id = None
    repo.schedule_image_cleanup(db, row)
    await db.delete(row)
    await db.commit()
    return Response(status_code=204)
