from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

Text = Annotated[str, Field(max_length=10000)]


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
