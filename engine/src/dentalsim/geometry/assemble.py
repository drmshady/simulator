"""Joins a placed assembly into one bonded OpenCASCADE model (research R4; conventions "Analysis assumptions").

1. **Build**: every placed block builds its parts in the current gmsh model, in the conventions frame.
2. **Fragment**: one OpenCASCADE boolean fragment of every part and cutter splits the space into pieces and makes
   touching parts share their faces, so the mesh shares nodes there and every interface is bonded (tied).
3. **Displace**: a piece several parts claim goes to the part that displaces the others (an implant takes the bone
   it sits in), directly or through a chain of claimants: inside an implant, the retention screw keeps its space and
   the bone around the implant has none of it. A piece no claimant may take is a collision, which the validity
   checks report first; here it is refused.
4. **Cut**: a piece a cutter claims is emptied when the cutter cuts its winner's component (the screw-access
   channel in the abutment), so a cemented crown still covers the channel.
5. **Name**: a physical volume group per part, the support surfaces of each support plane, the outer surface of
   each part (for the load patches and the result surfaces) and the surfaces of each refinement hint.

Nothing here knows a block type: parts, supports, hints and occlusal points come from the block contract.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import gmsh
import numpy as np

from dentalsim.assembly.placement import Assembly
from dentalsim.blocks.base import Cutter, OcclusalPoint, Part, RefinementHint, SupportPlane
from dentalsim.conventions.regions import ReportAnchor
from dentalsim.errors import DentalsimError, Refused
from dentalsim.geometry import occ as occ_tools

PLANE_TOLERANCE_MM = 1e-4
"""A surface lies on a support plane when every sampled point is this close to it."""
SLIVER_MM3 = 1e-3
"""A piece shared by two parts below this volume is boolean noise, given to the displacing part."""
_SAFE = re.compile(r"[^A-Za-z0-9_-]")


def solver_name(text: str, prefix: str) -> str:
    """A CalculiX-safe set name (letters, digits, `_`, `-`; a letter first; at most 60 characters)."""
    return (prefix + _SAFE.sub("_", text))[:60]


@dataclass(eq=False)
class AssembledPart:
    """One part of the joined model. `key` is `<instance>.<part>`, unique in the model."""

    key: str
    instance_id: str
    block_label: str
    part: Part
    volumes: list[int]
    physical_tag: int = 0
    surfaces: list[int] = field(default_factory=list)
    """Its faces on the outer surface of the whole model."""

    @property
    def component(self) -> str:
        return self.part.component

    @property
    def material(self) -> str:
        return self.part.material

    @property
    def set_name(self) -> str:
        return solver_name(self.key, "P_")


@dataclass(frozen=True)
class SupportSurface:
    name: str
    instance_id: str
    surfaces: tuple[int, ...]


@dataclass(frozen=True)
class HintSurface:
    hint: RefinementHint
    instance_id: str
    surfaces: tuple[int, ...]


@dataclass(frozen=True)
class BlockPoint:
    """A named occlusal point of a block instance."""

    instance_id: str
    point: OcclusalPoint


@dataclass(eq=False)
class AssembledModel:
    """The joined model in the current gmsh session, with everything the mesh and the solve need."""

    parts: list[AssembledPart]
    supports: list[SupportSurface]
    hints: list[HintSurface]
    occlusal_points: list[BlockPoint]
    anchors: list[ReportAnchor]
    bounds: tuple[tuple[float, float, float], tuple[float, float, float]]
    labels: dict[str, str] = field(default_factory=dict)
    """How messages name each block instance: "Crown 'crown-1'"."""
    parent_of: dict[str, str | None] = field(default_factory=dict)
    """The block instance each one is attached to (None for the root)."""

    def part(self, key: str) -> AssembledPart:
        for part in self.parts:
            if part.key == key:
                return part
        raise KeyError(key)

    def parts_of(self, instance_id: str) -> list[AssembledPart]:
        return [p for p in self.parts if p.instance_id == instance_id]

    def occlusal_point(self, instance_id: str, name: str) -> OcclusalPoint:
        for found in self.occlusal_points:
            if found.instance_id == instance_id and found.point.name == name:
                return found.point
        known = ", ".join(p.point.name for p in self.occlusal_points if p.instance_id == instance_id) or "none"
        raise Refused(
            f"block {instance_id!r} has no occlusal point {name!r} (it has {known})", code="validation_failed"
        )

    @property
    def size_mm(self) -> float:
        low, high = (np.asarray(b) for b in self.bounds)
        return float(np.linalg.norm(high - low))


def _winner(claimants: Sequence[AssembledPart]) -> AssembledPart | None:
    """The part that takes a piece several parts claim: the one that displaces every other claimant, directly or
    through a chain of claimants (inside an implant, a retention screw takes the space from the implant, which takes
    it from the bone). None when no claimant does: a collision."""
    for candidate in claimants:
        reached, frontier = {id(candidate)}, [candidate]
        while frontier:
            current = frontier.pop()
            for other in claimants:
                if id(other) not in reached and other.component in current.part.displaces:
                    reached.add(id(other))
                    frontier.append(other)
        if len(reached) == len(claimants):
            return candidate
    return None


def _join(parts: list[AssembledPart], cutters: Sequence[tuple[str, Cutter]]) -> None:
    """One boolean fragment of every part and cutter: touching parts then share faces, and every piece goes to its
    winner (`_winner`) unless a cutter claiming it empties the winner's component. A part left with nothing is
    refused, and so is a piece no claimant may take, unless it is boolean noise below `SLIVER_MM3`."""
    kernel = occ_tools.occ()
    inputs: list[tuple[AssembledPart | Cutter, int]] = [(p, tag) for p in parts for tag in p.volumes]
    inputs += [(c, tag) for _, c in cutters for tag in c.volumes]
    if len(inputs) < 2:
        return
    dim_tags = [(3, tag) for _, tag in inputs]
    _, pieces = kernel.fragment(dim_tags[:1], dim_tags[1:])
    claims: dict[int, list[AssembledPart | Cutter]] = {}
    for (owner, _), mapped in zip(inputs, pieces, strict=True):
        for dim, tag in mapped:
            if dim == 3 and owner not in claims.setdefault(tag, []):
                claims[tag].append(owner)
    for part in parts:
        part.volumes = []
    emptied: list[int] = []
    for tag, owners in sorted(claims.items()):
        claimants = [o for o in owners if isinstance(o, AssembledPart)]
        winner = _winner(claimants) if claimants else None
        if claimants and winner is None:
            volume = kernel.getMass(3, tag)
            if volume > SLIVER_MM3:
                names = " and ".join(f"{c.block_label} ({c.part.name})" for c in claimants)
                centre = tuple(round(c, 2) for c in kernel.getCenterOfMass(3, tag))
                raise Refused(
                    f"{names} overlap by {volume:.2f} mm³ near {centre}; check the scenario's validity first",
                    code="run_refused",
                )
            winner = claimants[0]
        cut = winner is None or any(isinstance(o, Cutter) and winner.component in o.cuts for o in owners)
        if cut:
            emptied.append(tag)
        else:
            assert winner is not None
            winner.volumes.append(tag)
    if emptied:
        kernel.remove(occ_tools.volumes(emptied), recursive=True)
    for part in parts:
        if not part.volumes:
            raise Refused(
                f"{part.block_label} part {part.part.name!r} lies wholly inside other parts, so nothing of it is left",
                code="run_refused",
            )


def _surface_points(tag: int) -> np.ndarray[Any, Any]:
    (u0, v0), (u1, v1) = (np.asarray(b) for b in zip(*gmsh.model.getParametrizationBounds(2, tag), strict=True))
    grid = [(u, v) for u in np.linspace(u0, u1, 4) for v in np.linspace(v0, v1, 4)]
    values = gmsh.model.getValue(2, tag, [c for uv in grid for c in uv])
    return np.asarray(values, dtype=np.float64).reshape(-1, 3)


def _on_plane(tag: int, plane: SupportPlane) -> bool:
    gmsh.option.setNumber("Geometry.OCCBoundsUseStl", 1)
    point, normal = np.asarray(plane.point), np.asarray(plane.normal) / np.linalg.norm(plane.normal)
    centre = np.asarray(occ_tools.occ().getCenterOfMass(2, tag))
    if abs(float((centre - point) @ normal)) > PLANE_TOLERANCE_MM:
        return False
    return bool(np.all(np.abs((_surface_points(tag) - point) @ normal) <= PLANE_TOLERANCE_MM))


def _outer_surfaces(volume_tags: Sequence[int]) -> set[int]:
    boundary = gmsh.model.getBoundary([(3, t) for t in volume_tags], combined=True, oriented=False)
    return {abs(tag) for dim, tag in boundary if dim == 2}


def _own_surfaces(volume_tags: Sequence[int]) -> set[int]:
    boundary = gmsh.model.getBoundary([(3, t) for t in volume_tags], combined=False, oriented=False)
    return {abs(tag) for dim, tag in boundary if dim == 2}


def assemble(assembly: Assembly) -> AssembledModel:
    """Join a complete assembly in the current gmsh model (open one with `dentalsim.geometry.occ.session`)."""
    if not assembly.complete:
        missing = ", ".join(b.instance_id for b in assembly.blocks if b.context is None)
        raise Refused(f"the assembly cannot be built: blocks {missing} cannot be placed", code="run_refused")
    parts: list[AssembledPart] = []
    cutters = []
    supports: list[tuple[str, SupportPlane]] = []
    hints: list[tuple[str, RefinementHint]] = []
    points: list[BlockPoint] = []
    anchors: list[ReportAnchor] = []
    for placed, ctx in assembly.placed():
        made = placed.block_type.build(ctx)
        for made_part in made.parts:
            key = f"{placed.instance_id}.{made_part.name}"
            parts.append(AssembledPart(key, placed.instance_id, placed.label, made_part, list(made_part.volumes)))
        cutters += [(placed.instance_id, c) for c in made.cutters]
        supports += [(placed.instance_id, s) for s in made.supports]
        hints += [(placed.instance_id, h) for h in made.hints]
        points += [BlockPoint(placed.instance_id, p) for p in placed.block_type.occlusal_points(ctx)]
        anchors += placed.block_type.report_anchors(ctx)

    _join(parts, cutters)
    occ_tools.remove_orphans()
    occ_tools.occ().synchronize()

    all_volumes = [t for p in parts for t in p.volumes]
    outer = _outer_surfaces(all_volumes)
    for part in parts:
        part.physical_tag = gmsh.model.addPhysicalGroup(3, part.volumes, name=part.key)
        part.surfaces = sorted(_own_surfaces(part.volumes) & outer)

    support_surfaces = []
    for instance_id, plane in supports:
        owned = {s for p in parts if p.instance_id == instance_id for s in p.surfaces}
        on_plane = tuple(sorted(s for s in owned if _on_plane(s, plane)))
        if not on_plane:
            raise DentalsimError(f"support {plane.name!r} of {instance_id!r} matches no outer surface of the model")
        gmsh.model.addPhysicalGroup(2, list(on_plane), name=f"support:{instance_id}.{plane.name}")
        support_surfaces.append(SupportSurface(plane.name, instance_id, on_plane))

    hint_surfaces = []
    for instance_id, hint in hints:
        hinted = next(p for p in parts if p.instance_id == instance_id and p.part.name == hint.part)
        hint_surfaces.append(HintSurface(hint, instance_id, tuple(sorted(_own_surfaces(hinted.volumes)))))

    low, high = occ_tools.bounding_box(all_volumes)
    return AssembledModel(
        parts,
        support_surfaces,
        hint_surfaces,
        points,
        anchors,
        (low, high),
        labels={b.instance_id: b.label for b in assembly.blocks},
        parent_of={b.instance_id: b.parent_id for b in assembly.blocks},
    )


def total_volume_mm3(model: AssembledModel) -> float:
    return math.fsum(occ_tools.volume_mm3(p.volumes) for p in model.parts)
