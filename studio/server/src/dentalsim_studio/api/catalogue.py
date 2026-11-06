"""The catalogue (task T085; contracts/studio-api.md "Catalogue"; FR-006, FR-011a).

- `GET /api/physics`: every capability with its status, required cases and the report that made it ready.
- `GET /api/blocks`: every block type the engine has, as the interface's palette shows it: its settings schema (with
  each setting's group, kind and limits), attach points, physics, result types, and whether it is ready or awaiting
  a benchmark. No route here knows a block type: everything comes from the block registry (SC-012).
- `GET /api/templates` and `POST /api/templates`: the built-in and owner templates; saving a scenario as an owner
  template.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from dentalsim.blocks.registry import catalogue
from dentalsim.errors import Conflict, NotFound
from dentalsim.physics.registry import CapabilitySummary, summaries
from dentalsim.templates.loader import builtin_templates, template_from_scenario
from dentalsim.templates.model import Branch, ProsthesisTemplate, ProsthesisType
from dentalsim_studio.api.deps import ReposDep
from dentalsim_studio.db import SqliteRepos

router = APIRouter(prefix="/api", tags=["catalogue"])

Readiness = Literal["ready", "awaiting_benchmark"]


class PhysicsItem(CapabilitySummary):
    report_url: str | None = None


def capability_states(repos: SqliteRepos) -> dict[str, CapabilitySummary]:
    return {s.id: s for s in summaries(repos.capabilities)}


@router.get("/physics", response_model=list[PhysicsItem])
def physics(repos: ReposDep) -> list[PhysicsItem]:
    return [
        PhysicsItem(
            **s.model_dump(),
            report_url=f"/api/verification/reports/{s.report_id}" if s.report_id else None,
        )
        for s in summaries(repos.capabilities)
    ]


def block_entries(repos: SqliteRepos) -> list[dict[str, Any]]:
    states = capability_states(repos)
    entries = []
    for block_type in catalogue().all():
        described = block_type.describe()
        waiting = sorted(c for c in block_type.physics if states[c].status != "ready")
        described["status"] = "ready" if not waiting else "awaiting_benchmark"
        described["awaiting"] = [states[c].label for c in waiting]
        for result in described["result_types"]:
            locked = [c for c in result["needs_physics"] if states[c].status != "ready"]
            result["status"] = "ready" if not locked and not waiting else "awaiting_benchmark"
            result["locked_message"] = f"not reported: needs {states[locked[0]].label.lower()}" if locked else None
        entries.append(described)
    return entries


@router.get("/blocks")
def blocks(repos: ReposDep) -> list[dict[str, Any]]:
    return block_entries(repos)


class TemplateSummary(BaseModel):
    id: str
    name: str
    prosthesis_type: str
    branch: str
    description: str
    origin: Literal["builtin", "owner"]
    approval: str | None
    """`proposed` or `approved` for built-in templates (their design defaults), None for owner templates."""
    fit_rules: list[dict[str, Any]]
    blocks: list[str]


def summary_of(template: ProsthesisTemplate) -> TemplateSummary:
    return TemplateSummary(
        id=template.id,
        name=template.name,
        prosthesis_type=template.prosthesis_type,
        branch=template.branch,
        description=template.description,
        origin=template.origin,
        approval=None if template.approval is None else template.approval.status,
        fit_rules=[{"rule": r.rule, **r.params()} for r in template.fit_rules],
        blocks=[f"{b.instance_id} ({b.type_id})" for b in template.blocks],
    )


def find_template(repos: SqliteRepos, template_id: str) -> ProsthesisTemplate:
    for template in builtin_templates():
        if template.id == template_id:
            return template
    owned = repos.scenarios.get_template(template_id)
    if owned is None:
        raise NotFound(f"there is no template {template_id}")
    return owned


@router.get("/templates", response_model=list[TemplateSummary])
def templates(repos: ReposDep) -> list[TemplateSummary]:
    return [summary_of(t) for t in [*builtin_templates(), *repos.scenarios.list_templates()]]


class SaveTemplate(BaseModel):
    scenario_id: str
    name: str = Field(min_length=1, max_length=120)
    template_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,60}$")
    branch: Branch
    prosthesis_type: ProsthesisType = "custom"
    description: str = ""


@router.post("/templates", response_model=TemplateSummary, status_code=201)
def save_template(body: SaveTemplate, repos: ReposDep) -> TemplateSummary:
    record = repos.scenarios.require(body.scenario_id)
    if any(t.id == body.template_id for t in builtin_templates()) or repos.scenarios.get_template(body.template_id):
        raise Conflict(f"a template with the id {body.template_id!r} already exists")

    def parameter_key(entry_id: str) -> str:
        entry = repos.library.get_entry(entry_id)
        if entry is None:
            raise NotFound(f"there is no library entry {entry_id}")
        return entry.parameter_key

    template = template_from_scenario(
        record.scenario,
        template_id=body.template_id,
        name=body.name,
        branch=body.branch,
        parameter_key_of=parameter_key,
        prosthesis_type=body.prosthesis_type,
        description=body.description,
    )
    repos.scenarios.add_template(template)
    return summary_of(template)
