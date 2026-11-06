"""Input records and input hashes: the keys of every stored mesh, preview and result (research R14, FR-058).

An input record lists everything that can change a stored item: the fully resolved scenario, the library entry
versions and their content hashes, the detail level, the conventions version, the hash of the result-affecting
engine source, the solver build and the gmsh version. The SHA-256 of its canonical JSON is the store key, so the same
inputs always give the same key and any change gives a new one.

The engine commit is deliberately not part of the record. It is kept on the run for provenance only, so a commit that
touches only the interface, the server or the documentation never forces a re-solve (Principle VI).
"""

from __future__ import annotations

import hashlib
import json
import math
import numbers
from collections.abc import Iterable, Mapping, Set
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticSerializationError, to_jsonable_python

from dentalsim import version as versioning
from dentalsim.model.run import LibraryVersionRef
from dentalsim.model.scenario import DetailLevel, Scenario

INPUT_FORMAT: Literal["dentalsim.input/1"] = "dentalsim.input/1"
SHA256_HEX = r"^[0-9a-f]{64}$"
PURPOSE = r"^[a-z][a-z0-9_]*$"
MAX_EXACT_INTEGER = 2**53
"""Larger integers cannot be written as floats exactly, so two different ones could share a hash."""


def canonical_json(value: Any) -> bytes:
    """The one JSON spelling of `value`, as UTF-8 bytes: equal inputs give identical bytes.

    - Keys are sorted, there is no whitespace, and non-ASCII text is written as UTF-8, not escaped.
    - Every number is written as a float with `repr(float(x))`, so 10, 10.0 and -0.0 versus 0.0 cannot give two
      hashes for one value. NaN, infinities and integers above 2**53 are refused. Booleans stay booleans.
    - Pydantic models are dumped field by field (`model_dump(mode="python")`) and then normalised like any other
      value. This is the JSON form, except that a set is sorted: `mode="json"` would list it in the set's
      arbitrary iteration order.
    - Tuples become lists; sets and frozensets become lists sorted by their canonical JSON. Dates, paths and other
      leaves are converted with pydantic's JSON rules.
    """
    return _dumps(_normalise(value)).encode("utf-8")


def _dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _normalise(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _normalise(value.model_dump(mode="python"))
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, numbers.Integral):
        return _integer(int(value))
    if isinstance(value, numbers.Real):
        return _float(float(value))
    if isinstance(value, Mapping):
        normalised: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"canonical JSON needs text keys, not {key!r}")
            normalised[key] = _normalise(item)
        return normalised
    if isinstance(value, list | tuple):
        return [_normalise(item) for item in value]
    if isinstance(value, Set):
        return sorted((_normalise(item) for item in value), key=_dumps)
    if isinstance(value, bytes | bytearray | memoryview):
        raise TypeError("canonical JSON cannot hold raw bytes")
    try:
        converted = to_jsonable_python(value)
    except PydanticSerializationError as exc:
        raise TypeError(f"cannot write a {type(value).__name__} as canonical JSON") from exc
    return _normalise(converted)


def _integer(value: int) -> float:
    if abs(value) > MAX_EXACT_INTEGER:
        raise ValueError(f"the integer {value} is too large to hash exactly")
    return float(value)


def _float(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("NaN and infinite numbers have no canonical form")
    return 0.0 if value == 0 else value


class InputRecord(BaseModel):
    """Everything that can change a stored item. `input_hash()` of the record is the item's store key.

    - `purpose` keeps different artefact kinds made from the same inputs apart, such as `run`, `preview` and `mesh`.
    - `scenario` is the fully resolved scenario as JSON, with every library value already chosen.
    - `library_versions` lists each library entry version used once, sorted by entry id and version. It may also
      hold versions the scenario does not name, such as strength references and Frost window edges.
    - `detail_level` and the scenario's own `detail_level` always agree; a record where they differ is refused.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    format: Literal["dentalsim.input/1"] = INPUT_FORMAT
    purpose: str = Field(pattern=PURPOSE)
    scenario: dict[str, Any]
    library_versions: tuple[LibraryVersionRef, ...]
    detail_level: DetailLevel
    conventions_version: str = Field(min_length=1)
    engine_source_hash: str = Field(pattern=SHA256_HEX)
    solver_build_id: str = Field(min_length=1)
    gmsh_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> InputRecord:
        if self.scenario.get("detail_level") != self.detail_level:
            raise ValueError(
                f"the scenario's detail level {self.scenario.get('detail_level')!r} differs from the record's "
                f"{self.detail_level!r}"
            )
        keys = [(ref.entry_id, ref.version) for ref in self.library_versions]
        if keys != sorted(set(keys)):
            raise ValueError("library versions must be sorted by entry id and version, each listed once")
        return self


def input_record(
    scenario: Scenario,
    *,
    library_versions: Iterable[LibraryVersionRef],
    detail_level: DetailLevel,
    solver_build_id: str,
    purpose: str = "run",
    conventions_version: str | None = None,
    engine_source_hash: str | None = None,
    gmsh_version: str | None = None,
) -> InputRecord:
    """The input record of one computation of `scenario`.

    - `detail_level` is the level actually computed and wins over the scenario's saved level: it is written into
      the recorded scenario too, so a preview of a scenario saved at `standard` records `preview` in both places.
    - `library_versions` must cover every library value in the scenario. Repeats of the same version are merged;
      the same version with two different content hashes is refused.
    - `conventions_version`, `engine_source_hash` and `gmsh_version` default to the running engine's: the loaded
      conventions, `dentalsim.version.engine_source_hash()` (never the commit) and the installed gmsh.
    """
    refs = _unique_refs(library_versions)
    listed = {(ref.entry_id, ref.version) for ref in refs}
    unlisted = sorted({(v.entry_id, v.entry_version) for v in scenario.library_values()} - listed)
    if unlisted:
        names = ", ".join(f"{entry_id} version {number}" for entry_id, number in unlisted)
        raise ValueError(f"the library versions do not include every library value of the scenario: {names}")
    if conventions_version is None:
        from dentalsim.conventions.loader import get_loaded_conventions

        conventions_version = get_loaded_conventions().version
    resolved = scenario.model_copy(update={"detail_level": detail_level})
    return InputRecord(
        purpose=purpose,
        scenario=resolved.model_dump(mode="json"),
        library_versions=tuple(refs),
        detail_level=detail_level,
        conventions_version=conventions_version,
        engine_source_hash=engine_source_hash if engine_source_hash is not None else versioning.engine_source_hash(),
        solver_build_id=solver_build_id,
        gmsh_version=gmsh_version if gmsh_version is not None else versioning.gmsh_version(),
    )


def _unique_refs(refs: Iterable[LibraryVersionRef]) -> list[LibraryVersionRef]:
    by_key: dict[tuple[str, int], LibraryVersionRef] = {}
    for ref in refs:
        key = (ref.entry_id, ref.version)
        known = by_key.setdefault(key, ref)
        if known != ref:
            raise ValueError(
                f"library entry {ref.entry_id} version {ref.version} is listed with two different contents"
            )
    return [by_key[key] for key in sorted(by_key)]


def input_hash(record: InputRecord) -> str:
    """The SHA-256 hex digest of the record's canonical JSON: the store key of what was computed from it."""
    return hashlib.sha256(canonical_json(record)).hexdigest()
