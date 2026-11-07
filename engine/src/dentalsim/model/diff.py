"""The settings that differ between two scenarios (task T115; FR-027, FR-032).

Generic: blocks are matched by `instance_id` and load cases by `id`, and every field is compared by walking the
models, so no block type is named here (SC-012). The scenario name is not a setting and is left out.

Paths are written as the search and the editor write them:

- `<block>.<setting>` for a block setting (`implant-1.length_mm`)
- `<block>.type`, `<block>.attach_to`, `<block>.placement.<field>` for the rest of a block
- `load:<id>.<field>` for a load case (`load:axial.force`, `load:axial.direction.angle_deg`)
- a top-level field (`detail_level`, `conventions_version`, `brand_comparison`)

A block or load case present in only one scenario is one difference (`added` or `removed`), not one per field.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from dentalsim.model.scenario import BlockInstance, LoadCase, Scenario
from dentalsim.model.settings import LibraryValue

Change = Literal["changed", "added", "removed"]
Group = Literal["block", "setting", "placement", "load", "scenario"]
TOP_LEVEL = ("detail_level", "conventions_version", "brand_comparison")
REL_TOLERANCE = 1e-9


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Difference(_Model):
    path: str
    change: Change
    before: Any = None
    """The value in the first scenario: a number, text or list, a library value as
    `{value, entry_id, entry_version, choice}`, or a whole block or load case when it was removed."""
    after: Any = None
    block: str | None = None
    load: str | None = None
    group: Group


class ScenarioDiff(_Model):
    differences: list[Difference]

    @property
    def paths(self) -> list[str]:
        return [d.path for d in self.differences]

    def for_block(self, instance_id: str) -> list[Difference]:
        return [d for d in self.differences if d.block == instance_id]


def _plain(value: Any) -> Any:
    if isinstance(value, LibraryValue):
        return {"value": value.value, "entry_id": value.entry_id, "entry_version": value.entry_version,
                "choice": value.choice}  # fmt: skip
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, tuple):
        return [_plain(v) for v in value]
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _equal(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= REL_TOLERANCE * max(1.0, abs(a), abs(b))
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_equal(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_equal(a[k], b[k]) for k in a)
    return bool(a == b)


def _flatten(prefix: str, value: Any) -> dict[str, Any]:
    """A nested dict as `prefix.key...` leaves; lists and other values are leaves."""
    if isinstance(value, dict):
        found: dict[str, Any] = {}
        for key, inner in value.items():
            found.update(_flatten(f"{prefix}.{key}", inner))
        return found
    return {prefix: value}


def _block_fields(block: BlockInstance) -> dict[str, tuple[Any, Group]]:
    """Path → (value, group) for every field of a block."""
    name = block.instance_id
    fields: dict[str, tuple[Any, Group]] = {
        f"{name}.type": (f"{block.type_id} {block.type_version}", "block"),
        f"{name}.attach_to": (None if block.attach_to is None else _plain(block.attach_to), "block"),
    }
    for path, value in _flatten(f"{name}.placement", _plain(block.placement)).items():
        fields[path] = (value, "placement")
    for setting_id, value in block.settings.items():
        fields[f"{name}.{setting_id}"] = (_plain(value.value if not isinstance(value, LibraryValue) else value),
                                          "setting")  # fmt: skip
    return fields


def _load_fields(load: LoadCase) -> dict[str, tuple[Any, Group]]:
    prefix = f"load:{load.id}"
    dumped = load.model_dump(mode="json", exclude={"id", "force", "cycles", "direction"})
    fields: dict[str, tuple[Any, Group]] = {f"{prefix}.force": (_plain(load.force), "load")}
    if load.cycles is not None:
        fields[f"{prefix}.cycles"] = (_plain(load.cycles), "load")
    if load.direction is not None:
        for path, value in _flatten(f"{prefix}.direction", _plain(load.direction)).items():
            fields[path] = (value, "load")
    for key, value in dumped.items():
        fields[f"{prefix}.{key}"] = (value, "load")
    return fields


def _compare_fields(
    before: Mapping[str, tuple[Any, Group]],
    after: Mapping[str, tuple[Any, Group]],
    *,
    block: str | None = None,
    load: str | None = None,
) -> list[Difference]:
    found = []
    for path in [*before, *(p for p in after if p not in before)]:
        old, new = before.get(path), after.get(path)
        if old is not None and new is not None and _equal(old[0], new[0]):
            continue
        change: Change = "added" if old is None else "removed" if new is None else "changed"
        group = (new if old is None else old)[1]  # type: ignore[index]
        found.append(
            Difference(
                path=path,
                change=change,
                before=None if old is None else old[0],
                after=None if new is None else new[0],
                block=block,
                load=load,
                group=group,
            )
        )
    return found


def diff_scenarios(first: Scenario, second: Scenario) -> ScenarioDiff:
    """Every setting that differs from `first` to `second`, in the order of `first` (then what `second` adds)."""
    differences: list[Difference] = []
    for field in TOP_LEVEL:
        old, new = _plain(getattr(first, field)), _plain(getattr(second, field))
        if not _equal(old, new):
            differences.append(Difference(path=field, change="changed", before=old, after=new, group="scenario"))

    blocks_after = {b.instance_id: b for b in second.blocks}
    blocks_before = {b.instance_id: b for b in first.blocks}
    for block in first.blocks:
        other = blocks_after.get(block.instance_id)
        if other is None:
            differences.append(Difference(path=block.instance_id, change="removed", before=_plain(block),
                                          block=block.instance_id, group="block"))  # fmt: skip
        else:
            differences += _compare_fields(_block_fields(block), _block_fields(other), block=block.instance_id)
    for block in second.blocks:
        if block.instance_id not in blocks_before:
            differences.append(Difference(path=block.instance_id, change="added", after=_plain(block),
                                          block=block.instance_id, group="block"))  # fmt: skip

    loads_after = {load.id: load for load in second.loads}
    loads_before = {load.id: load for load in first.loads}
    for load in first.loads:
        other_load = loads_after.get(load.id)
        if other_load is None:
            differences.append(Difference(path=f"load:{load.id}", change="removed", before=_plain(load),
                                          load=load.id, group="load"))  # fmt: skip
        else:
            differences += _compare_fields(_load_fields(load), _load_fields(other_load), load=load.id)
    for load in second.loads:
        if load.id not in loads_before:
            differences.append(Difference(path=f"load:{load.id}", change="added", after=_plain(load),
                                          load=load.id, group="load"))  # fmt: skip
    return ScenarioDiff(differences=differences)
