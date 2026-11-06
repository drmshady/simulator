"""Projects and scenarios (task T086; contracts/studio-api.md "Projects and scenarios"; FR-010, FR-011, FR-014,
FR-032, FR-052).

- Projects at their minimum: list, create, read (with their scenarios), rename and delete.
- `POST /api/scenarios` makes a scenario from a template (with the owner's jaw segment settings, if given) or
  duplicates one; a template that does not fit answers its `template_misfit` problems with the new scenario.
- `GET /api/scenarios/{id}` adds what the editor shows beside the definition: each library value with its entry,
  version, range and whether it lies outside it, the template misfits, and the block-version upgrades on offer.
- `PUT /api/scenarios/{id}` saves when the body carries the current revision (otherwise 409 `conflict`); every
  library value must point to an approved version (otherwise 422), and its `outside_range` is recomputed.
- `POST /api/scenarios/{id}/validate` runs the validity checks (the fast ones unless `mode=full`), stores the result
  and emits `validity.changed`.
- `POST /api/scenarios/{id}/geometry` answers the builder's scene: block surfaces and keep-out volumes as glTF.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Query, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from dentalsim.assembly.placement import place
from dentalsim.assembly.validity import check_validity
from dentalsim.blocks.registry import catalogue
from dentalsim.clock import new_ulid, utc_now
from dentalsim.errors import NotFound, Refused
from dentalsim.geometry.surfaces import builder_glb
from dentalsim.library.service import LibraryService
from dentalsim.model.scenario import BlockInstance, LoadCase, Scenario
from dentalsim.model.settings import LibraryValue, SettingValue
from dentalsim.model.validity import Problem, ValidityResult
from dentalsim.persistence import DerivedFrom, ProjectRecord, ScenarioRecord
from dentalsim.templates.fit import fit_problems
from dentalsim.templates.loader import open_template
from dentalsim_studio.api.catalogue import find_template
from dentalsim_studio.api.deps import ReposDep
from dentalsim_studio.db import SqliteRepos
from dentalsim_studio.events import bus

router = APIRouter(prefix="/api", tags=["scenarios"])


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- projects ----------------------------------------------------------------------------------------------------


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
    updated_at: AwareDatetime


class ProjectView(ProjectRecord):
    scenarios: list[ScenarioSummary] = []


def _summary(record: ScenarioRecord) -> ScenarioSummary:
    return ScenarioSummary(
        id=record.id,
        name=record.name,
        revision=record.revision,
        valid=None if record.validity is None else record.validity.ok,
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
    scenarios = [_summary(r) for r in repos.scenarios.list_for_project(project_id)]
    return ProjectView(**project.model_dump(), scenarios=scenarios)


@router.patch("/projects/{project_id}", response_model=ProjectRecord)
def update_project(project_id: str, body: ProjectPatch, repos: ReposDep) -> ProjectRecord:
    return repos.scenarios.update_project(project_id, name=body.name, notes=body.notes, tags=body.tags)


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str, repos: ReposDep) -> Response:
    repos.scenarios.delete_project(project_id)
    return Response(status_code=204)


# --- scenarios ---------------------------------------------------------------------------------------------------


class FromTemplate(_Body):
    template_id: str
    jaw_settings: dict[str, SettingValue] | None = None
    """The owner's jaw segment settings, replacing the template's."""


class FromScenario(_Body):
    scenario_id: str


class CreateScenario(_Body):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    project_id: str
    name: str | None = Field(default=None, min_length=1, max_length=200)
    from_: FromTemplate | FromScenario = Field(alias="from")


class SaveScenario(_Body):
    revision: int = Field(ge=1)
    scenario: Scenario
    name: str | None = Field(default=None, min_length=1, max_length=200)
    notes: str | None = None
    tags: list[str] | None = None


class LibraryValueView(BaseModel):
    """One library value of the scenario, as the editor shows it (FR-015, FR-052)."""

    path: str
    """`<block>.<setting>` or `load:<id>.force`, `load:<id>.cycles`."""
    entry_id: str
    entry_version: int
    parameter_key: str | None = None
    choice: str
    value: float
    units: str | None = None
    typical: float | None = None
    range_min: float | None = None
    range_max: float | None = None
    outside_range: bool = False
    error: str | None = None
    """Set when the value no longer points to an approved version (for example, it was rejected)."""


class Upgrade(BaseModel):
    instance_id: str
    type_id: str
    from_version: str
    to_version: str


class NamedPoint(BaseModel):
    """A named occlusal point of a placed block, where a load can act."""

    block: str
    point: str
    label: str
    position_mm: tuple[float, float, float]


class ScenarioView(ScenarioRecord):
    library: list[LibraryValueView] = []
    misfits: list[Problem] = []
    upgrades: list[Upgrade] = []
    occlusal_points: list[NamedPoint] = []


def occlusal_points_of(scenario: Scenario) -> list[NamedPoint]:
    try:
        assembly = place(scenario)
    except (Refused, NotFound):
        return []
    return [
        NamedPoint(block=placed.instance_id, point=p.name, label=p.label, position_mm=p.position_mm)
        for placed, ctx in assembly.placed()
        for p in placed.block_type.occlusal_points(ctx)
    ]


def _library_paths(scenario: Scenario) -> list[tuple[str, LibraryValue]]:
    found: list[tuple[str, LibraryValue]] = []
    for block in scenario.blocks:
        found += [(f"{block.instance_id}.{k}", v) for k, v in block.settings.items() if isinstance(v, LibraryValue)]
    for load in scenario.loads:
        found.append((f"load:{load.id}.force", load.force))
        if load.cycles is not None:
            found.append((f"load:{load.id}.cycles", load.cycles))
    return found


def library_view(scenario: Scenario, library: LibraryService) -> list[LibraryValueView]:
    views = []
    for path, value in _library_paths(scenario):
        base = {"path": path, "entry_id": value.entry_id, "entry_version": value.entry_version,
                "choice": value.choice, "value": value.value}  # fmt: skip
        try:
            resolved = library.resolve_value(value)
        except (Refused, NotFound) as exc:
            views.append(LibraryValueView(**base, outside_range=value.outside_range, error=str(exc)))
            continue
        views.append(
            LibraryValueView(
                **base,
                parameter_key=resolved.parameter_key,
                units=resolved.units,
                typical=resolved.typical,
                range_min=resolved.range_min,
                range_max=resolved.range_max,
                outside_range=resolved.outside_range,
            )
        )
    return views


def upgrades_of(scenario: Scenario) -> list[Upgrade]:
    found = catalogue()
    offers = []
    for block in scenario.blocks:
        latest = found.latest(block.type_id)
        if latest.version != block.type_version:
            offers.append(Upgrade(instance_id=block.instance_id, type_id=block.type_id,
                                  from_version=block.type_version, to_version=latest.version))  # fmt: skip
    return offers


def misfits_of(repos: SqliteRepos, record: ScenarioRecord) -> list[Problem]:
    if record.derived_from is None or record.derived_from.kind != "template":
        return []
    try:
        template = find_template(repos, record.derived_from.id)
    except NotFound:
        return []
    return fit_problems(template, record.scenario)


def view(repos: SqliteRepos, record: ScenarioRecord) -> ScenarioView:
    return ScenarioView(
        **record.model_dump(),
        library=library_view(record.scenario, LibraryService(repos)),
        misfits=misfits_of(repos, record),
        upgrades=upgrades_of(record.scenario),
        occlusal_points=occlusal_points_of(record.scenario),
    )


def checked_scenario(scenario: Scenario, library: LibraryService) -> Scenario:
    """The scenario with each library value checked against an approved version (422 otherwise) and its
    `outside_range` recomputed; its blocks must be readable (unknown types, wrong setting kinds: 422). A block's
    missing library values, and those whose parameter key its design values have changed, are filled with the
    typical value of the current approved version."""

    def checked(value: LibraryValue) -> LibraryValue:
        return value.model_copy(update={"outside_range": library.resolve_value(value).outside_range})

    found = catalogue()
    blocks: list[BlockInstance] = []
    for block in scenario.blocks:
        block_type = found.for_instance(block)
        keys = block_type.parameter_keys(block_type.design_values(block.settings))
        settings: dict[str, SettingValue] = {}
        for setting_id, value in block.settings.items():
            settings[setting_id] = checked(value) if isinstance(value, LibraryValue) else value
        for setting_id, key in keys.items():
            # A library setting that is missing, or whose entry no longer matches the design values (another
            # material, say), takes the typical value of the entry the design values now name.
            current = settings.get(setting_id)
            entry = library.get_entry(current.entry_id) if isinstance(current, LibraryValue) else None
            if entry is None or entry.parameter_key != key:
                settings[setting_id] = library.resolve(key).library_value()
        blocks.append(block.model_copy(update={"settings": settings}))
    loads: list[LoadCase] = []
    for load in scenario.loads:
        update: dict[str, Any] = {"force": checked(load.force)}
        if load.cycles is not None:
            update["cycles"] = checked(load.cycles)
        loads.append(load.model_copy(update=update))
    result = scenario.model_copy(update={"blocks": blocks, "loads": loads})
    place(result)
    return result


def _validate(repos: SqliteRepos, record: ScenarioRecord, mode: Literal["fast", "full"]) -> ValidityResult:
    result = check_validity(
        record.scenario, mode=mode, scenario_revision=record.revision, extra=misfits_of(repos, record)
    )
    repos.scenarios.set_validity(record.id, result)
    bus.emit(
        repos,
        "validity.changed",
        {"scenario_id": record.id, "revision": record.revision, "ok": result.ok, "problems": len(result.problems)},
    )
    return result


class Created(BaseModel):
    scenario: ScenarioView
    misfits: list[Problem]


@router.post("/scenarios", response_model=Created, status_code=201)
def create_scenario(body: CreateScenario, repos: ReposDep) -> Created:
    if repos.scenarios.get_project(body.project_id) is None:
        raise NotFound(f"there is no project {body.project_id}")
    library = LibraryService(repos)
    source = body.from_
    if isinstance(source, FromTemplate):
        template = find_template(repos, source.template_id)
        opened = open_template(template, library, jaw_settings=source.jaw_settings, name=body.name)
        scenario, derived = opened.scenario, DerivedFrom(kind="template", id=template.id)
    else:
        original = repos.scenarios.require(source.scenario_id)
        scenario = original.scenario.model_copy(update={"name": body.name or f"{original.name} (copy)"})
        derived = DerivedFrom(kind="scenario", id=original.id)
    now = utc_now()
    record = ScenarioRecord(
        id=new_ulid(), project_id=body.project_id, name=body.name or scenario.name, derived_from=derived,
        scenario=checked_scenario(scenario, library), created_at=now, updated_at=now,
    )  # fmt: skip
    repos.scenarios.add(record)
    _validate(repos, record, "fast")
    stored = repos.scenarios.require(record.id)
    return Created(scenario=view(repos, stored), misfits=misfits_of(repos, stored))


@router.get("/scenarios/{scenario_id}", response_model=ScenarioView)
def get_scenario(scenario_id: str, repos: ReposDep) -> ScenarioView:
    return view(repos, repos.scenarios.require(scenario_id))


@router.put("/scenarios/{scenario_id}", response_model=ScenarioView)
def save_scenario(scenario_id: str, body: SaveScenario, repos: ReposDep) -> ScenarioView:
    current = repos.scenarios.require(scenario_id)
    scenario = checked_scenario(body.scenario, LibraryService(repos))
    changed = current.model_copy(
        update={
            "scenario": scenario,
            "name": body.name or current.name,
            "notes": current.notes if body.notes is None else body.notes,
            "tags": current.tags if body.tags is None else body.tags,
        }
    )
    saved = repos.scenarios.save(changed, expected_revision=body.revision)
    return view(repos, saved)


@router.post("/scenarios/{scenario_id}/validate", response_model=ValidityResult)
def validate(
    scenario_id: str, repos: ReposDep, mode: Literal["fast", "full"] = Query(default="fast")
) -> ValidityResult:
    return _validate(repos, repos.scenarios.require(scenario_id), mode)


@router.post("/scenarios/{scenario_id}/geometry")
def geometry(scenario_id: str, repos: ReposDep) -> Response:
    record = repos.scenarios.require(scenario_id)
    glb = builder_glb(place(record.scenario))
    return Response(glb, media_type="model/gltf-binary", headers={"X-Scenario-Revision": str(record.revision)})
