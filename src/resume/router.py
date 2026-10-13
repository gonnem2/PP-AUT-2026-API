from copy import deepcopy
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.common.db.models import ImportedProject, Profile, Resume
from src.common.db.session import get_async_session
from src.resume import github
from src.resume.schemas import (
    AboutInput,
    Contacts,
    Content,
    ImportProjects,
    Repository,
    Title,
)
from src.user.dto import UserDTO

router = APIRouter(prefix="/api/v1", tags=["resumehub"])
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


async def owned(db: AsyncSession, user_id: int, resume_id: UUID) -> Resume:
    row = await db.scalar(
        select(Resume).where(Resume.id == str(resume_id), Resume.user_id == user_id)
    )
    if row is None:
        raise HTTPException(404, "Резюме не найдено")
    return row


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
    await db.delete(await owned(db, user.id, resume_id))
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
    return (await owned(db, user.id, resume_id)).content


@router.put("/resumes/{resume_id}/content", status_code=204)
async def save_content(resume_id: UUID, data: Content, db: DB, user: User):
    row = await owned(db, user.id, resume_id)
    row.content = data.model_dump()
    await db.commit()
    return Response(status_code=204)


@router.get("/resumes/{resume_id}/pdf")
async def export_pdf(resume_id: UUID, db: DB, user: User):
    await owned(db, user.id, resume_id)
    raise HTTPException(501, "Экспорт PDF будет подключён через отдельный сервис.")


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
    return [row.data for row in rows]


@router.post("/projects/import", status_code=204)
async def import_projects(data: ImportProjects, db: DB, user: User):
    # Only public repositories; metadata comes from GitHub, never from client input.
    async with github.client() as client:
        for repo_id in dict.fromkeys(data.repoIds):
            remote = await github.get(client, f"/repositories/{repo_id}")
            if remote.get("private"):
                raise HTTPException(404, "Публичный репозиторий не найден")
            value = github.repository(remote)
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
