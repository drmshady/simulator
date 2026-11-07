"""Projects (task T117; contracts/studio-api.md "Projects and scenarios"; FR-031).

- `GET /api/projects`, `POST /api/projects`
- `GET /api/projects/{id}`: the project with its scenarios (notes, tags, where each came from), its runs and its
  studies, so the projects page needs one request
- `PATCH /api/projects/{id}`: name, notes or tags
- `DELETE /api/projects/{id}`: only an empty project (otherwise 409 `conflict`)
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from dentalsim.errors import NotFound
from dentalsim.persistence import DerivedFrom, ProjectRecord, ScenarioRecord
from dentalsim_studio.api.deps import ReposDep
from dentalsim_studio.db.repos.runs import RunSummary
from dentalsim_studio.db.repos.scenarios import StudySummary

router = APIRouter(prefix="/api", tags=["projects"])


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectBody(_Body):
    name: str = Field(min_length=1, max_length=200)
    notes: str = ""
    tags: list[str] = []


class ProjectPatch(_Body):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    notes: str | None = None
    tags: list[str] | None = None


class ScenarioSummary(BaseModel):
    id: str
    name: str
    revision: int
    valid: bool | None
    notes: str = ""
    tags: list[str] = []
    derived_from: DerivedFrom | None = None
    updated_at: AwareDatetime


class ProjectView(ProjectRecord):
    scenarios: list[ScenarioSummary] = []
    runs: list[RunSummary] = []
    """Every run of the project's scenarios, newest first."""
    studies: list[StudySummary] = []


def scenario_summary(record: ScenarioRecord) -> ScenarioSummary:
    return ScenarioSummary(
        id=record.id,
        name=record.name,
        revision=record.revision,
        valid=None if record.validity is None else record.validity.ok,
        notes=record.notes,
        tags=record.tags,
        derived_from=record.derived_from,
        updated_at=record.updated_at,
    )


@router.get("/projects", response_model=list[ProjectRecord])
def list_projects(repos: ReposDep) -> list[ProjectRecord]:
    return repos.scenarios.list_projects()


@router.post("/projects", response_model=ProjectRecord, status_code=201)
def create_project(body: ProjectBody, repos: ReposDep) -> ProjectRecord:
    return repos.scenarios.add_project(body.name, notes=body.notes, tags=body.tags)


@router.get("/projects/{project_id}", response_model=ProjectView)
def get_project(project_id: str, repos: ReposDep) -> ProjectView:
    project = repos.scenarios.get_project(project_id)
    if project is None:
        raise NotFound(f"there is no project {project_id}")
    return ProjectView(
        **project.model_dump(),
        scenarios=[scenario_summary(r) for r in repos.scenarios.list_for_project(project_id)],
        runs=repos.runs.summaries_for_project(project_id),
        studies=repos.scenarios.list_studies(project_id),
    )


@router.patch("/projects/{project_id}", response_model=ProjectRecord)
def update_project(project_id: str, body: ProjectPatch, repos: ReposDep) -> ProjectRecord:
    return repos.scenarios.update_project(project_id, name=body.name, notes=body.notes, tags=body.tags)


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str, repos: ReposDep) -> Response:
    repos.scenarios.delete_project(project_id)
    return Response(status_code=204)
