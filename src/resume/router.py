import asyncio
from copy import deepcopy
from typing import Annotated
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.common.db.models import ImportedProject, Profile, Resume
from src.common.db.session import get_async_session
from src.images import repo as image_repo
from src.images.processing import MAX_OUTPUT_BYTES
from src.images.repo import owned_resume as owned
from src.images.storage import StorageUnavailable, get_image_storage
from src.resume import github
from src.resume.pdf import render_resume_pdf
from src.resume.schemas import (
    AboutInput,
    Contacts,
    Content,
    ImportProjects,
    Repository,
    Title,
)
from src.resume.utils import content_etag
from src.user.dto import UserDTO

router = APIRouter(prefix="/api/v1", tags=["resumehub"])
pdf_semaphore = asyncio.Semaphore(2)
DB = Annotated[AsyncSession, Depends(get_async_session)]
User = Annotated[UserDTO, Depends(get_current_user)]


def summary(row: Resume) -> dict:
    content = Content.model_validate(row.content)

    def count(items):
        return sum(
            any(
                str(v).strip()
                for k, v in item.model_dump().items()
                if k not in ("id", "repoId") and v is not None
            )
            for item in items
        )

    return {
        "id": row.id,
        "title": row.title,
        "status": "draft",
        "position": content.contacts.position.strip() or None,
        "projectsCount": count(content.projects),
        "workplacesCount": count(content.experience),
        "updatedAt": row.updated_at.isoformat(),
    }


@router.get("/resumes")
async def list_resumes(db: DB, user: User):
    rows = await db.scalars(
        select(Resume)
        .where(Resume.user_id == user.id)
        .order_by(Resume.updated_at.desc())
    )
    return [summary(row) for row in rows]


@router.post("/resumes", status_code=201)
async def create_resume(data: Title, db: DB, user: User):
    profile = await db.scalar(select(Profile).where(Profile.user_id == user.id))
    content = Content(
        contacts=Contacts(
            fullName=profile.full_name if profile else user.name,
            position=profile.specialization if profile else "",
            email=user.email,
        ),
        about=profile.about if profile else "",
    )
    row = Resume(
        id=str(uuid4()), user_id=user.id, title=data.title, content=content.model_dump()
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return summary(row)


@router.get("/resumes/{resume_id}")
async def get_resume(resume_id: UUID, db: DB, user: User):
    return summary(await owned(db, user.id, resume_id))


@router.patch("/resumes/{resume_id}")
async def rename_resume(resume_id: UUID, data: Title, db: DB, user: User):
    row = await owned(db, user.id, resume_id)
    row.title = data.title
    await db.commit()
    await db.refresh(row)
    return summary(row)


@router.delete("/resumes/{resume_id}", status_code=204)
async def delete_resume(resume_id: UUID, db: DB, user: User):
    resume = await owned(db, user.id, resume_id, lock=True)
    for image in await image_repo.gallery(db, resume.id):
        image_repo.schedule_image_cleanup(db, image)
    await db.delete(resume)
    await db.commit()
    return Response(status_code=204)


@router.post("/resumes/{resume_id}/duplicate", status_code=201)
async def duplicate_resume(resume_id: UUID, db: DB, user: User):
    source = await owned(db, user.id, resume_id)
    row = Resume(
        id=str(uuid4()),
        user_id=user.id,
        title=source.title[:192] + " (копия)",
        content=deepcopy(source.content),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return summary(row)


@router.get("/resumes/{resume_id}/content", response_model=Content)
async def get_content(resume_id: UUID, db: DB, user: User):
    content = Content.model_validate((await owned(db, user.id, resume_id)).content)
    return Response(
        content.model_dump_json(),
        media_type="application/json",
        headers={"ETag": content_etag(content), "Cache-Control": "no-store"},
    )


@router.put("/resumes/{resume_id}/content", status_code=204)
async def save_content(
    resume_id: UUID,
    data: Content,
    db: DB,
    user: User,
    if_match: Annotated[str | None, Header()] = None,
):
    row = await owned(db, user.id, resume_id, lock=True)
    if if_match is not None and if_match != content_etag(
        Content.model_validate(row.content)
    ):
        raise HTTPException(
            412,
            "Резюме изменено в другой вкладке. Загрузите актуальную версию; ваши правки сохранены в локальном черновике.",
        )
    row.content = data.model_dump()
    await db.commit()
    return Response(status_code=204, headers={"ETag": content_etag(data)})


@router.get("/resumes/{resume_id}/pdf")
async def export_pdf(resume_id: UUID, db: DB, user: User):
    row = await owned(db, user.id, resume_id)
    content = Content.model_validate(row.content)
    title = row.title
    avatar_key = await image_repo.current_avatar_key(db, row)
    # Чтение завершено до генерации файла: CPU-работа не удерживает транзакцию.
    await db.rollback()
    async with pdf_semaphore:
        avatar = None
        if avatar_key:
            try:
                avatar = await get_image_storage().get(avatar_key, MAX_OUTPUT_BYTES)
            except StorageUnavailable as error:
                raise HTTPException(
                    503, "Не удалось загрузить аватар для PDF"
                ) from error
        body = await asyncio.to_thread(render_resume_pdf, content, title, avatar)
    return Response(
        body,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=resume-{resume_id}.pdf; filename*=UTF-8''{quote(title + '.pdf', safe='')}",
            "Cache-Control": "no-store",
        },
    )


@router.get("/github/repos", response_model=list[Repository])
async def github_repos(
    user: User,
    username: Annotated[
        str, Query(pattern=r"^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,37}[a-zA-Z0-9])?$")
    ],
):
    repos = []
    async with github.client() as client:
        for page in range(1, 11):
            data = await github.get(
                client,
                f"/users/{username}/repos",
                per_page=100,
                page=page,
                sort="updated",
            )
            repos.extend(
                github.repository(item) for item in data if not item.get("private")
            )
            if len(data) < 100:
                break
    return repos


@router.get("/projects", response_model=list[Repository])
async def projects(db: DB, user: User):
    rows = await db.scalars(
        select(ImportedProject)
        .where(ImportedProject.user_id == user.id)
        .order_by(ImportedProject.created_at.desc())
    )
    # Evidence is read separately; keep the strict metadata response backwards compatible.
    return [
        {
            key: value
            for key, value in row.data.items()
            if key in Repository.model_fields
        }
        for row in rows
    ]


@router.post("/projects/import", status_code=204)
async def import_projects(data: ImportProjects, db: DB, user: User):
    fetched: list[tuple[str, dict]] = []
    async with github.client() as client:
        for repo_id in dict.fromkeys(data.repoIds):
            remote = await github.get(client, f"/repositories/{repo_id}")
            if remote.get("private"):
                raise HTTPException(404, "Публичный репозиторий не найден")
            value = github.repository(remote)
            fetched.append((repo_id, value))
    for repo_id, value in fetched:
        existing = await db.scalar(
            select(ImportedProject)
            .where(
                ImportedProject.user_id == user.id,
                ImportedProject.repo_id == repo_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if existing is not None:
            value = {**existing.data, **value}
        stmt = insert(ImportedProject).values(
            user_id=user.id, repo_id=repo_id, data=value
        )
        await db.execute(
            stmt.on_conflict_do_update(
                index_elements=["user_id", "repo_id"], set_={"data": value}
            )
        )
    await db.commit()
    return Response(status_code=204)


@router.post("/ai/improve-about")
async def improve_about(data: AboutInput, user: User):
    raise HTTPException(
        503, "ИИ-редактор пока не настроен. Текст можно редактировать вручную."
    )
