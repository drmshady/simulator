"""Out-of-date detection for stored meshes, previews and results (FR-058, FR-051, research R14).

Each time a stored item is opened, its input record is compared with the current engine, conventions and library:

- **out_of_date** (FR-058): the result-affecting engine source, the solver build, the gmsh version, the conventions
  version, or the content of a library entry version it used is no longer what it was. A library version that no
  longer exists counts as changed.
- **needs_reverification** (FR-051): the conventions version changed.

Nothing stale is reused silently: `assert_reusable()` refuses, listing every reason in plain language.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from pydantic import BaseModel, ConfigDict

from dentalsim import version as versioning
from dentalsim.errors import Refused
from dentalsim.model.run import RunFlag
from dentalsim.persistence import Repos
from dentalsim.store.hashing import InputRecord

LibraryLookup = Callable[[str, int], str | None]
"""(entry id, version) → that version's current content hash, or None when the version no longer exists."""


class CurrentState(BaseModel):
    """What the engine, conventions and library are now.

    - `solver_build_id` is None when no solver is configured; the solver build is then not compared, and the
      staleness result says so in its notes.
    - `library` gives a library version's current content hash: a mapping keyed by (entry id, version), or a
      function such as the one `from_repos()` builds.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    engine_source_hash: str
    solver_build_id: str | None
    gmsh_version: str
    conventions_version: str
    library: Mapping[tuple[str, int], str] | LibraryLookup

    def library_content_hash(self, entry_id: str, version: int) -> str | None:
        if isinstance(self.library, Mapping):
            return self.library.get((entry_id, version))
        return self.library(entry_id, version)

    @classmethod
    def from_repos(
        cls,
        repos: Repos,
        *,
        solver_build_id: str | None,
        engine_source_hash: str | None = None,
        gmsh_version: str | None = None,
        conventions_version: str | None = None,
    ) -> CurrentState:
        """The current state, reading library versions through `repos.library.get_version` as they are needed.

        The engine source hash, gmsh version and conventions version default to the running engine's. Use the
        result on the thread that owns `repos`.
        """
        library = repos.library

        def lookup(entry_id: str, version: int) -> str | None:
            found = library.get_version(entry_id, version)
            return found.content_hash if found is not None else None

        if conventions_version is None:
            from dentalsim.conventions.loader import get_loaded_conventions

            conventions_version = get_loaded_conventions().version
        return cls(
            engine_source_hash=engine_source_hash
            if engine_source_hash is not None
            else versioning.engine_source_hash(),
            solver_build_id=solver_build_id,
            gmsh_version=gmsh_version if gmsh_version is not None else versioning.gmsh_version(),
            conventions_version=conventions_version,
            library=lookup,
        )


class Staleness(BaseModel):
    """The flags a stored item gets, with one plain-language reason per changed input.

    `changed` names the changed inputs in the same order as `reasons`: `engine_source_hash`, `solver_build_id`,
    `gmsh_version`, `conventions_version`, or `library:<entry id>/<version>`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    flags: set[RunFlag] = set()
    reasons: list[str] = []
    changed: list[str] = []
    notes: list[str] = []

    @property
    def out_of_date(self) -> bool:
        return "out_of_date" in self.flags

    @property
    def needs_reverification(self) -> bool:
        return "needs_reverification" in self.flags

    @property
    def reusable(self) -> bool:
        return not self.flags


def check_staleness(record: InputRecord, current: CurrentState) -> Staleness:
    flags: set[RunFlag] = set()
    reasons: list[str] = []
    changed: list[str] = []
    notes: list[str] = []

    def stale(item: str, reason: str) -> None:
        flags.add("out_of_date")
        changed.append(item)
        reasons.append(reason)

    if record.engine_source_hash != current.engine_source_hash:
        stale("engine_source_hash", "The engine code that affects results has changed since this was computed.")
    if current.solver_build_id is None:
        notes.append("No solver is configured, so the solver build was not compared.")
    elif record.solver_build_id != current.solver_build_id:
        stale(
            "solver_build_id",
            f"The solver build has changed from {record.solver_build_id} to {current.solver_build_id}.",
        )
    if record.gmsh_version != current.gmsh_version:
        stale("gmsh_version", f"gmsh has changed from version {record.gmsh_version} to {current.gmsh_version}.")
    if record.conventions_version != current.conventions_version:
        stale(
            "conventions_version",
            f"The conventions have changed from version {record.conventions_version} to "
            f"{current.conventions_version}, so this needs re-verification.",
        )
        flags.add("needs_reverification")
    for ref in record.library_versions:
        now = current.library_content_hash(ref.entry_id, ref.version)
        if now == ref.content_hash:
            continue
        what = f"Library entry {ref.parameter_key} version {ref.version}"
        if now is None:
            stale(f"library:{ref.entry_id}/{ref.version}", f"{what} no longer exists in the library.")
        else:
            stale(f"library:{ref.entry_id}/{ref.version}", f"{what} no longer has the content this was computed with.")
    return Staleness(flags=flags, reasons=reasons, changed=changed, notes=notes)


def assert_reusable(record: InputRecord, current: CurrentState) -> Staleness:
    """The staleness of a reusable item (its notes may still say what was not compared); refuses a stale one."""
    staleness = check_staleness(record, current)
    if staleness.reusable:
        return staleness
    raise Refused(
        "This stored item is out of date and cannot be reused; it has to be computed again. "
        + " ".join(staleness.reasons),
        code="out_of_date",
        missing=[
            {"item": f"input:{item}", "message": reason}
            for item, reason in zip(staleness.changed, staleness.reasons, strict=True)
        ],
    )
