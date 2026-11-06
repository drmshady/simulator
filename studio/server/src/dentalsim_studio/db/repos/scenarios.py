"""The `projects`, `scenarios` and `owner_templates` tables (task T084).

Projects are kept at their minimum here (name, notes, tags); Story 3 extends them. A scenario carries a revision:
a save names the revision it was edited from, and is refused with `Conflict` when the stored one has moved on.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from dentalsim.clock import new_ulid, utc_now
from dentalsim.errors import Conflict, NotFound
from dentalsim.model.scenario import Scenario
from dentalsim.model.validity import ValidityResult
from dentalsim.persistence import DerivedFrom, ProjectRecord, ScenarioRecord
from dentalsim.templates.model import ProsthesisTemplate
from dentalsim_studio.db.connection import Database, from_json, from_time, to_json, to_time


def _project(row: sqlite3.Row) -> ProjectRecord:
    return ProjectRecord(
        id=row["id"],
        name=row["name"],
        notes=row["notes"],
        tags=from_json(row["tags"]),
        created_at=from_time(row["created_at"]),
        updated_at=from_time(row["updated_at"]),
    )


def _scenario(row: sqlite3.Row) -> ScenarioRecord:
    derived = from_json(row["derived_from"])
    validity = from_json(row["validity"])
    return ScenarioRecord(
        id=row["id"],
        project_id=row["project_id"],
        name=row["name"],
        notes=row["notes"],
        tags=from_json(row["tags"]),
        derived_from=None if derived is None else DerivedFrom.model_validate(derived),
        revision=row["revision"],
        scenario=Scenario.model_validate(from_json(row["definition"])),
        validity=None if validity is None else ValidityResult.model_validate(validity),
        created_at=from_time(row["created_at"]),
        updated_at=from_time(row["updated_at"]),
    )


class SqliteScenarioRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    # --- projects ---------------------------------------------------------------------------------------------

    def add_project(self, name: str, *, notes: str = "", tags: Sequence[str] = ()) -> ProjectRecord:
        now = utc_now()
        record = ProjectRecord(id=new_ulid(), name=name, notes=notes, tags=list(tags), created_at=now, updated_at=now)
        self.db.execute(
            "INSERT INTO projects (id, name, notes, tags, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (record.id, record.name, record.notes, to_json(record.tags), to_time(now), to_time(now)),
        )
        return record

    def get_project(self, project_id: str) -> ProjectRecord | None:
        row = self.db.one("SELECT * FROM projects WHERE id = ?", (project_id,))
        return None if row is None else _project(row)

    def list_projects(self) -> list[ProjectRecord]:
        return [_project(row) for row in self.db.all("SELECT * FROM projects ORDER BY updated_at DESC, id DESC")]

    def update_project(
        self, project_id: str, *, name: str | None = None, notes: str | None = None, tags: Sequence[str] | None = None
    ) -> ProjectRecord:
        project = self.get_project(project_id)
        if project is None:
            raise NotFound(f"there is no project {project_id}")
        changed = project.model_copy(
            update={
                "name": project.name if name is None else name,
                "notes": project.notes if notes is None else notes,
                "tags": project.tags if tags is None else list(tags),
                "updated_at": utc_now(),
            }
        )
        self.db.execute(
            "UPDATE projects SET name = ?, notes = ?, tags = ?, updated_at = ? WHERE id = ?",
            (changed.name, changed.notes, to_json(changed.tags), to_time(changed.updated_at), project_id),
        )
        return changed

    def delete_project(self, project_id: str) -> None:
        if self.db.one("SELECT 1 FROM scenarios WHERE project_id = ? LIMIT 1", (project_id,)) is not None:
            raise Conflict("the project still holds scenarios; delete or move them first")
        if self.db.execute("DELETE FROM projects WHERE id = ?", (project_id,)).rowcount == 0:
            raise NotFound(f"there is no project {project_id}")

    # --- scenarios --------------------------------------------------------------------------------------------

    def add(self, record: ScenarioRecord) -> None:
        if self.get_project(record.project_id) is None:
            raise NotFound(f"there is no project {record.project_id}")
        self.db.execute(
            """INSERT INTO scenarios (id, project_id, name, notes, tags, derived_from, definition, revision, validity,
                                      created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.id,
                record.project_id,
                record.name,
                record.notes,
                to_json(record.tags),
                None if record.derived_from is None else to_json(record.derived_from.model_dump(mode="json")),
                to_json(record.scenario.model_dump(mode="json")),
                record.revision,
                None if record.validity is None else to_json(record.validity.model_dump(mode="json")),
                to_time(record.created_at),
                to_time(record.updated_at),
            ),
        )

    def get(self, scenario_id: str) -> ScenarioRecord | None:
        row = self.db.one("SELECT * FROM scenarios WHERE id = ?", (scenario_id,))
        return None if row is None else _scenario(row)

    def require(self, scenario_id: str) -> ScenarioRecord:
        record = self.get(scenario_id)
        if record is None:
            raise NotFound(f"there is no scenario {scenario_id}")
        return record

    def list_for_project(self, project_id: str) -> list[ScenarioRecord]:
        rows = self.db.all(
            "SELECT * FROM scenarios WHERE project_id = ? ORDER BY updated_at DESC, id DESC", (project_id,)
        )
        return [_scenario(row) for row in rows]

    def save(self, record: ScenarioRecord, *, expected_revision: int) -> ScenarioRecord:
        """Store the record as revision `expected_revision + 1`; `Conflict` when the stored revision differs."""
        saved = record.model_copy(update={"revision": expected_revision + 1, "updated_at": utc_now(), "validity": None})
        with self.db.transaction():
            cursor = self.db.execute(
                """UPDATE scenarios SET name = ?, notes = ?, tags = ?, definition = ?, revision = ?, validity = NULL,
                                       updated_at = ?
                   WHERE id = ? AND revision = ?""",
                (
                    saved.name,
                    saved.notes,
                    to_json(saved.tags),
                    to_json(saved.scenario.model_dump(mode="json")),
                    saved.revision,
                    to_time(saved.updated_at),
                    record.id,
                    expected_revision,
                ),
            )
            if cursor.rowcount == 0:
                current = self.get(record.id)
                if current is None:
                    raise NotFound(f"there is no scenario {record.id}")
                raise Conflict(
                    f"the scenario changed while it was being edited (now revision {current.revision}, the edit "
                    f"started from {expected_revision}); reload it and apply the change again"
                )
        return saved

    def set_validity(self, scenario_id: str, validity: ValidityResult) -> None:
        self.db.execute(
            "UPDATE scenarios SET validity = ? WHERE id = ? AND revision = ?",
            (to_json(validity.model_dump(mode="json")), scenario_id, validity.scenario_revision),
        )

    # --- owner templates --------------------------------------------------------------------------------------

    def add_template(self, template: ProsthesisTemplate) -> None:
        self.db.execute(
            """INSERT INTO owner_templates (id, name, prosthesis_type, branch, definition, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                template.id,
                template.name,
                template.prosthesis_type,
                template.branch,
                to_json(template.model_dump(mode="json", by_alias=True)),
                to_time(utc_now()),
            ),
        )

    def get_template(self, template_id: str) -> ProsthesisTemplate | None:
        row = self.db.one("SELECT definition FROM owner_templates WHERE id = ?", (template_id,))
        return None if row is None else ProsthesisTemplate.model_validate(from_json(row["definition"]))

    def list_templates(self) -> list[ProsthesisTemplate]:
        rows = self.db.all("SELECT definition FROM owner_templates ORDER BY created_at, id")
        return [ProsthesisTemplate.model_validate(from_json(row["definition"])) for row in rows]
