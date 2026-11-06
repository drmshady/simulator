"""The block registry: finds every block module under `dentalsim/blocks/` and indexes its block types.

A block module exports `BLOCK_TYPES`, a tuple of `BlockType` instances. The registry imports each module once, so
adding a block type means adding a module and nothing else (FR-006, SC-012). Generic code, including the physics
gate (`catalogue()`), reaches block types only through here.

`type_version` is pinned per block instance (contracts/scenario-format.md), so lookups name a version; a version
the engine no longer has is refused, and the newest version is offered instead.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterable
from functools import cache
from typing import Any

from dentalsim.blocks.base import SEMVER, SLICE, TYPE_ID, BlockType
from dentalsim.errors import DentalsimError, Refused
from dentalsim.model.scenario import BlockInstance
from dentalsim.physics.registry import get_capability

PACKAGE = "dentalsim.blocks"
FRAMEWORK_MODULES = frozenset({"base", "registry"})

AnyBlockType = BlockType[Any]


class UnknownBlockType(Refused):
    """A scenario names a block type or version this engine does not have."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="validation_failed")


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _validate(block_type: AnyBlockType, module: str) -> None:
    where = f"{module}: {type(block_type).__name__}"
    if not TYPE_ID.match(getattr(block_type, "type_id", "")):
        raise DentalsimError(f"{where} needs a type_id of lower-case letters, digits and underscores")
    if not SEMVER.match(getattr(block_type, "version", "")):
        raise DentalsimError(f"{where}: version must be a semantic version such as 1.0.0")
    if not SLICE.match(getattr(block_type, "slice", "")):
        raise DentalsimError(f"{where}: slice must be 1a, 1b or story9:<physics>")
    try:
        block_type.setting_specs()
    except TypeError as exc:
        raise DentalsimError(f"{where}: {exc}") from exc
    result_ids = [r.id for r in block_type.result_types]
    if len(result_ids) != len(set(result_ids)):
        raise DentalsimError(f"{where}: result type ids must be unique")
    undeclared = sorted(
        {region for r in block_type.result_types for region in r.regions} - set(block_type.report_regions)
    )
    if undeclared:
        raise DentalsimError(f"{where}: result types name regions missing from report_regions: {undeclared}")
    for capability in block_type.physics | {c for r in block_type.result_types for c in r.needs_physics}:
        get_capability(capability)
    names = [a.name for a in block_type.attach_points]
    if len(names) != len(set(names)):
        raise DentalsimError(f"{where}: attach point names must be unique")


@cache
def _index() -> dict[str, dict[str, AnyBlockType]]:
    package = importlib.import_module(PACKAGE)
    index: dict[str, dict[str, AnyBlockType]] = {}
    for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda i: i.name):
        if info.name in FRAMEWORK_MODULES or info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{PACKAGE}.{info.name}")
        exported = getattr(module, "BLOCK_TYPES", None)
        if not isinstance(exported, tuple) or not exported or not all(isinstance(b, BlockType) for b in exported):
            raise DentalsimError(f"block module {module.__name__} must export BLOCK_TYPES, a tuple of BlockType")
        for block_type in exported:
            _validate(block_type, module.__name__)
            versions = index.setdefault(block_type.type_id, {})
            if block_type.version in versions:
                raise DentalsimError(f"block type {block_type.type_id} {block_type.version} is registered twice")
            versions[block_type.version] = block_type
    return index


class Catalogue:
    """Every registered block type, by type id and version."""

    def __init__(self, index: dict[str, dict[str, AnyBlockType]]) -> None:
        self._index = index

    def type_ids(self) -> list[str]:
        return sorted(self._index)

    def latest(self, type_id: str) -> AnyBlockType:
        versions = self._index.get(type_id)
        if not versions:
            known = ", ".join(sorted(self._index)) or "none"
            raise UnknownBlockType(f"there is no block type {type_id!r}; the known ones are {known}")
        return versions[max(versions, key=_version_key)]

    def block_type(self, type_id: str, version: str | None = None) -> AnyBlockType:
        latest = self.latest(type_id)
        if version is None:
            return latest
        found = self._index[type_id].get(version)
        if found is None:
            raise UnknownBlockType(
                f"block type {type_id} has no version {version}; upgrade the block to {latest.version}"
            )
        return found

    def for_instance(self, instance: BlockInstance) -> AnyBlockType:
        return self.block_type(instance.type_id, instance.type_version)

    def all(self, *, every_version: bool = False) -> list[AnyBlockType]:
        """The newest version of each block type (or every version), by slice, then type id."""
        found: Iterable[AnyBlockType]
        if every_version:
            found = [b for versions in self._index.values() for b in versions.values()]
        else:
            found = [self.latest(type_id) for type_id in self._index]
        return sorted(found, key=lambda b: (b.slice, b.type_id, _version_key(b.version)))


def catalogue() -> Catalogue:
    return Catalogue(_index())


def all_block_types(*, every_version: bool = False) -> list[AnyBlockType]:
    return catalogue().all(every_version=every_version)


def block_type(type_id: str, version: str | None = None) -> AnyBlockType:
    return catalogue().block_type(type_id, version)
