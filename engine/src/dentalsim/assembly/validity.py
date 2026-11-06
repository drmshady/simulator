"""Validity checks of an assembly (FR-011; data-model.md "ValidityResult").

A scenario with an error can be saved, but not previewed or run. The checks, in order:

1. **Design limits**: each block's settings and placement within its limits (`design_limit`).
2. **Block rules**: each placed block's own `check()`, which sees the bone and the other blocks' keep-out volumes
   (an implant longer than the bone gives `exceeds_bone`, one in the nerve canal `in_nerve_canal`).
3. **Keep-out intrusions** of any part into another block's keep-out volume, by OpenCASCADE intersection, for
   the blocks whose own rules did not already report that intrusion.
4. **Overlaps** between parts of different blocks, by OpenCASCADE intersection: a common volume above
   `OVERLAP_TOLERANCE_MM3` is an `overlap`, located at its centroid. Overlaps a part is meant to take are not
   collisions: an implant displaces bone, an abutment the crown and the implant it enters (`Part.displaces`), and
   a chain of them settles an overlap too (inside the implant, the screw that displaces it meets no bone).

The **fast** mode, for checks while the owner builds, runs 1 and 2 only: they use the blocks' analytic volumes
and take well under a second. The **full** mode adds 3 and 4, and is what previews and runs require.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import gmsh
import numpy as np

from dentalsim.assembly.placement import Assembly, PlacedBlock, place
from dentalsim.blocks.base import KeepOut, Part
from dentalsim.blocks.registry import Catalogue
from dentalsim.conventions.schema import Conventions
from dentalsim.geometry import occ as occ_tools
from dentalsim.model.scenario import Scenario
from dentalsim.model.validity import Problem, ValidityResult

CheckMode = Literal["fast", "full"]

OVERLAP_TOLERANCE_MM3 = 0.01
"""A common volume below this is a touching contact or tessellation noise, not a collision."""
BOX_MARGIN_MM = 1e-3


@dataclass(frozen=True)
class _Solid:
    owner: PlacedBlock
    part: Part
    box: tuple[np.ndarray[Any, Any], np.ndarray[Any, Any]]


def _round(point: Sequence[float]) -> tuple[float, float, float]:
    return (round(float(point[0]), 3), round(float(point[1]), 3), round(float(point[2]), 3))


def _boxes_meet(a: _Solid, b: _Solid) -> bool:
    return bool(np.all(a.box[0] <= b.box[1] + BOX_MARGIN_MM) and np.all(b.box[0] <= a.box[1] + BOX_MARGIN_MM))


def _common(
    a: Sequence[int], b: Sequence[int], minus: Sequence[int] = ()
) -> tuple[float, tuple[float, float, float] | None]:
    """The volume both sets of solids share, less `minus`, and its centroid; the originals are kept."""
    kernel = occ_tools.occ()
    made, _ = kernel.intersect(occ_tools.volumes(a), occ_tools.volumes(b), removeObject=False, removeTool=False)
    pieces = [tag for dim, tag in made if dim == 3]
    if pieces and minus:
        made, _ = kernel.cut(occ_tools.volumes(pieces), occ_tools.volumes(minus), removeObject=True, removeTool=False)
        pieces = [tag for dim, tag in made if dim == 3]
    if not pieces:
        return 0.0, None
    masses = np.array([kernel.getMass(3, t) for t in pieces])
    centres = np.array([kernel.getCenterOfMass(3, t) for t in pieces])
    kernel.remove([(3, t) for t in pieces], recursive=True)
    total = float(masses.sum())
    if total <= 0:
        return 0.0, None
    return total, _round((masses[:, None] * centres).sum(axis=0) / total)


def _expected(a: Part, b: Part) -> bool:
    """Whether one of the two parts is meant to take the other's place where they overlap."""
    return b.component in a.displaces or a.component in b.displaces


def _part_label(solid: _Solid) -> str:
    return f"{solid.owner.label} ({solid.part.name})"


def build_solids(assembly: Assembly) -> list[_Solid]:
    """Build every placed block's parts in the current gmsh model, in the conventions frame."""
    solids = []
    for placed, ctx in assembly.placed():
        made = placed.block_type.build(ctx)
        for part in made.parts:
            low, high = occ_tools.bounding_box(part.volumes)
            solids.append(_Solid(placed, part, (np.array(low), np.array(high))))
    return solids


def _between(a: Part, b: Part, solids: Sequence[_Solid]) -> list[int]:
    """Volumes of the parts that settle an overlap of `a` and `b` through a chain of displacement: inside an
    implant, a retention screw (which displaces the implant) and the bone (which the implant displaces) never meet."""
    tags: list[int] = []
    for c in solids:
        if c.part is a or c.part is b:
            continue
        if (c.part.component in a.displaces and b.component in c.part.displaces) or (
            c.part.component in b.displaces and a.component in c.part.displaces
        ):
            tags += c.part.volumes
    return tags


def overlap_problems(solids: Sequence[_Solid]) -> list[Problem]:
    problems = []
    for i, a in enumerate(solids):
        for b in solids[i + 1 :]:
            if a.owner is b.owner or _expected(a.part, b.part) or not _boxes_meet(a, b):
                continue
            volume, centre = _common(a.part.volumes, b.part.volumes, _between(a.part, b.part, solids))
            if volume > OVERLAP_TOLERANCE_MM3:
                problems.append(
                    Problem(
                        code="overlap",
                        message=f"{_part_label(a)} and {_part_label(b)} overlap by {volume:.2f} mm³.",
                        block=a.owner.instance_id,
                        location_mm=centre,
                    )
                )
    return problems


def intrusion_problems(
    solids: Sequence[_Solid], keep_outs: Sequence[KeepOut], already: set[tuple[str | None, str]]
) -> list[Problem]:
    """Parts entering another block's keep-out volume, unless the block already reported that intrusion."""
    problems = []
    built: dict[int, list[int]] = {}
    for solid in solids:
        for keep_out in keep_outs:
            if keep_out.owner == solid.owner.instance_id or (solid.owner.instance_id, keep_out.code) in already:
                continue
            low, high = keep_out.volume.bounds()
            if np.any(np.asarray(high) < solid.box[0] - BOX_MARGIN_MM) or np.any(
                np.asarray(low) > solid.box[1] + BOX_MARGIN_MM
            ):
                continue
            tags = built.setdefault(id(keep_out), keep_out.volume.build_occ())
            volume, centre = _common(solid.part.volumes, tags)
            if volume > OVERLAP_TOLERANCE_MM3:
                problems.append(
                    Problem(
                        code=keep_out.code,
                        message=f"{_part_label(solid)} enters {keep_out.label} ({volume:.2f} mm³).",
                        block=solid.owner.instance_id,
                        location_mm=centre,
                    )
                )
                already.add((solid.owner.instance_id, keep_out.code))
    return problems


def block_problems(assembly: Assembly) -> list[Problem]:
    found: list[Problem] = []
    for placed, ctx in assembly.placed():
        found += placed.block_type.check(ctx)
    return found


def all_keep_outs(assembly: Assembly) -> list[KeepOut]:
    found: list[KeepOut] = []
    for placed, ctx in assembly.placed():
        found += placed.block_type.keep_out(ctx)
    return found


def check_assembly(assembly: Assembly, *, mode: CheckMode = "full") -> list[Problem]:
    """Every problem of a placed assembly; `full` adds the OpenCASCADE intrusion and overlap checks."""
    problems = list(assembly.problems) + block_problems(assembly)
    if mode == "full":
        already: set[tuple[str | None, str]] = {(p.block, p.code) for p in problems}
        with occ_tools.session("validity"):
            solids = build_solids(assembly)
            problems += intrusion_problems(solids, all_keep_outs(assembly), already)
            gmsh.model.occ.synchronize()
            problems += overlap_problems(solids)
    return problems


def check_validity(
    scenario: Scenario,
    *,
    mode: CheckMode = "full",
    scenario_revision: int | None = None,
    conventions: Conventions | None = None,
    blocks: Catalogue | None = None,
    extra: Sequence[Problem] = (),
) -> ValidityResult:
    """The validity of a scenario (FR-011). `extra` adds problems found elsewhere, such as a template misfit.

    Refused (`validation_failed`) when the scenario cannot be read at all (see `assembly.placement.place`).
    """
    assembly = place(scenario, conventions=conventions, blocks=blocks)
    problems = [*extra, *check_assembly(assembly, mode=mode)]
    return ValidityResult.from_problems(problems, scenario_revision=scenario_revision)
