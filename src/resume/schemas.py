from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Text = Annotated[str, Field(max_length=10000)]
GitHubUsername = Annotated[
    str, Field(pattern=r"^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,37}[a-zA-Z0-9])?$")
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Contacts(StrictModel):
    fullName: Text = ""
    position: Text = ""
    email: Text = ""
    location: Text = ""


class Experience(StrictModel):
    id: str = Field(max_length=100)
    company: Text = ""
    role: Text = ""
    period: Text = ""
    achievements: Text = ""


class Project(StrictModel):
    id: str = Field(max_length=100)
    name: Text = ""
    technologies: Text = ""
    contribution: Text = ""
    repoId: str | None = Field(default=None, max_length=100)


class Content(StrictModel):
    contacts: Contacts = Field(default_factory=Contacts)
    about: Text = ""
    experience: list[Experience] = Field(default_factory=list, max_length=100)
    projects: list[Project] = Field(default_factory=list, max_length=100)
    skills: Text = ""
    education: Text = ""
    additional: Text = ""


class Title(StrictModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)


class Repository(StrictModel):
    id: str
    name: str
    description: str
    tags: list[str]


class ImportProjects(StrictModel):
    repoIds: list[Annotated[str, Field(pattern=r"^[0-9]{1,20}$")]] = Field(
        min_length=1, max_length=100
    )


class AboutInput(StrictModel):
    text: str = Field(min_length=1, max_length=2000)


class GitHubCollectionOptions(StrictModel):
    username: GitHubUsername
    since: datetime | None = None
    until: datetime | None = None
    maxPullRequests: int = Field(default=10, ge=1, le=30)
    maxCommits: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def validate_period(self):
        for value in (self.since, self.until):
            if value is not None and (
                value.utcoffset() is None or not 1970 <= value.year <= 2099
            ):
                raise ValueError("Даты должны содержать часовой пояс и год 1970–2099")
        if self.since and self.until and self.since > self.until:
            raise ValueError("since не может быть позже until")
        return self


class GitHubCollectionRequest(GitHubCollectionOptions):
    repository: str = Field(
        pattern=r"^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,37}[a-zA-Z0-9])?/[a-zA-Z0-9_.-]{1,100}$"
    )


class GitHubEvidence(StrictModel):
    schemaVersion: Literal[1] = 1
    collectedAt: str
    subject: dict
    repository: dict
    period: dict
    coverage: dict
    pullRequests: list[dict]
    commits: list[dict]
    issues: list[dict]
    workflowRuns: list[dict]
    deployments: list[dict]
    limitations: list[str]
