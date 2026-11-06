"""Placement: turns a scenario's attach chain into block contexts in the conventions frame (FR-004, FR-010).

The assembly is a tree. Its root is the one block that attaches to nothing (the jaw segment, whose block type
attaches to no socket); every other block attaches to a socket its parent offers, by name.

**Placement offsets.** A block sits at its parent's socket, then moves by its own placement, given in the socket's
frame (for a block on the jaw, the frame's own axes: x mesial, y buccal, z occlusal):

    local = T(position_mm) · R_y(mesiodistal) · R_x(−buccolingual) · R_z(rotation_deg)

so a positive mesiodistal angle tilts the block's +z toward the socket's +x (mesial), a positive buccolingual angle
tilts it toward +y (buccal), and the rotation turns the block about its own axis (an angled abutment's tilt to any
side). The root is not moved: it defines the frame.

**The conventions frame.** The root's local frame has its origin at the crest centre of the first site, +x mesial,
+y buccal and +z occlusal. The conventions put +z along the long axis of the reference implant (the first block,
in scenario order, whose type is an axis reference), so when that block is tilted the whole assembly is turned
about the origin by the smallest rotation that makes its axis +z. Mirrored quadrants need nothing here: the jaw
segment builds them as mirror-image arches, so no reflection reaches the geometry or the mesh.

A block whose settings break a design limit has no context, and neither do the blocks attached below it: they
cannot be placed until it is fixed. Their problems are reported; the rest of the assembly is still placed.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from dentalsim.blocks.base import BlockContext, Environment, KeepOut, ParentLink, SettingsReading, Socket
from dentalsim.blocks.registry import AnyBlockType, Catalogue, catalogue
from dentalsim.conventions.loader import get_conventions
from dentalsim.conventions.schema import Conventions
from dentalsim.errors import Refused
from dentalsim.geometry.shapes import Volume
from dentalsim.geometry.transform import IDENTITY, Transform
from dentalsim.model.scenario import BlockInstance, Placement, Scenario
from dentalsim.model.validity import Problem

MAX_AXIS_ANGLE_DEG = 45.0
"""Largest mesiodistal or buccolingual tilt a placement may give a block, relative to its socket."""


def placement_transform(placement: Placement) -> Transform:
    """The placement offset in the socket's frame: T(position) · R_y(md) · R_x(−bl) · R_z(rotation)."""
    angles = placement.axis_angles_deg
    return (
        Transform.translation_by(placement.position_mm)
        @ Transform.rotation_about((0.0, 1.0, 0.0), angles.mesiodistal)
        @ Transform.rotation_about((1.0, 0.0, 0.0), -angles.buccolingual)
        @ Transform.rotation_about((0.0, 0.0, 1.0), placement.rotation_deg)
    )


def axis_alignment(axis: tuple[float, float, float]) -> Transform:
    """The smallest rotation about the origin that turns the unit vector `axis` into +z."""
    a = np.asarray(axis, dtype=np.float64)
    a = a / np.linalg.norm(a)
    z = np.array([0.0, 0.0, 1.0])
    cross = np.cross(a, z)
    sine, cosine = float(np.linalg.norm(cross)), float(a @ z)
    if sine < 1e-12:
        return IDENTITY if cosine > 0 else Transform.rotation_about((1.0, 0.0, 0.0), 180.0)
    return Transform.rotation_about(cross, math.degrees(math.atan2(sine, cosine)))


@dataclass(frozen=True, eq=False)
class PlacedBlock:
    """One block instance of the assembly.

    `context` is None when the block cannot be placed: its settings break a design limit, or a block it hangs from
    could not be placed.
    """

    instance: BlockInstance
    block_type: AnyBlockType
    reading: SettingsReading[Any]
    parent_id: str | None
    context: BlockContext[Any] | None

    @property
    def instance_id(self) -> str:
        return self.instance.instance_id

    @property
    def label(self) -> str:
        return f"{self.block_type.label} {self.instance_id!r}"


@dataclass(frozen=True, eq=False)
class Assembly:
    """The placed blocks, root first and every parent before its children, in the conventions frame."""

    scenario: Scenario
    blocks: tuple[PlacedBlock, ...]
    frame: Transform
    """The root's local frame in the conventions frame (a rotation about the origin; identity when the reference
    implant stands upright)."""
    axis_reference: str | None
    """The block instance whose axis is the frame's +z, if there is one."""
    problems: tuple[Problem, ...]
    """Placement problems: design limits of the settings and of the placements."""

    def block(self, instance_id: str) -> PlacedBlock:
        for placed in self.blocks:
            if placed.instance_id == instance_id:
                return placed
        raise KeyError(instance_id)

    def placed(self) -> Iterator[tuple[PlacedBlock, BlockContext[Any]]]:
        """The blocks that could be placed, with their contexts."""
        for placed in self.blocks:
            if placed.context is not None:
                yield placed, placed.context

    @property
    def complete(self) -> bool:
        """Every block is placed."""
        return all(placed.context is not None for placed in self.blocks)

    @property
    def root(self) -> PlacedBlock:
        return self.blocks[0]


def _children(scenario: Scenario) -> dict[str | None, list[BlockInstance]]:
    children: dict[str | None, list[BlockInstance]] = {}
    for block in scenario.blocks:
        parent = block.attach_to.instance_id if block.attach_to is not None else None
        children.setdefault(parent, []).append(block)
    return children


def _tree_order(scenario: Scenario, types: Mapping[str, AnyBlockType]) -> list[BlockInstance]:
    """The blocks root first, each parent before its children (siblings in scenario order)."""
    children = _children(scenario)
    roots = children.get(None, [])
    if len(roots) != 1:
        found = ", ".join(repr(b.instance_id) for b in roots) or "none"
        raise Refused(
            f"A scenario needs exactly one block that attaches to nothing (the root of the assembly); found {found}.",
            code="validation_failed",
            problems=[{"block": b.instance_id, "message": "attach it to a socket of another block"} for b in roots],
        )
    root = roots[0]
    root_type = types[root.instance_id]
    if root_type.attaches_to:
        wanted = " or ".join(repr(name) for name in root_type.attaches_to)
        raise Refused(
            f"{root_type.label} {root.instance_id!r} attaches to nothing, but it must be attached to a {wanted} "
            "socket; the root of an assembly is a jaw segment.",
            code="validation_failed",
            problems=[{"block": root.instance_id, "message": f"attach it to a {wanted} socket"}],
        )
    order, stack = [], [root]
    while stack:
        block = stack.pop(0)
        order.append(block)
        stack[:0] = children.get(block.instance_id, [])
    return order


def _check_attachment(block: BlockInstance, block_type: AnyBlockType, parent_type: AnyBlockType) -> None:
    assert block.attach_to is not None
    point = block.attach_to.attach_point
    offered = [a.name for a in parent_type.attach_points]
    problems = []
    if point not in block_type.attaches_to:
        wanted = " or ".join(repr(name) for name in block_type.attaches_to) or "no socket"
        problems.append(f"{block_type.label} {block.instance_id!r} attaches to {wanted}, not to {point!r}")
    elif point not in offered:
        problems.append(
            f"{parent_type.label} {block.attach_to.instance_id!r} offers no {point!r} socket "
            f"(it offers {', '.join(repr(o) for o in offered) or 'none'})"
        )
    if problems:
        raise Refused(
            "The scenario's attachments are not valid: " + "; ".join(problems) + ".",
            code="validation_failed",
            problems=[{"block": block.instance_id, "message": m} for m in problems],
        )


def _occupancy_problems(
    order: Sequence[BlockInstance], types: Mapping[str, AnyBlockType], blocked: set[str]
) -> list[Problem]:
    """A socket holds at most its capacity of blocks. The blocks beyond it, in tree order, are reported at once and
    left unplaced (added to `blocked`), so the full check does not report them again as a volume overlap."""
    problems = []
    holders: dict[tuple[str, str], list[str]] = {}
    for block in order:
        if block.attach_to is None:
            continue
        parent_type = types[block.attach_to.instance_id]
        point = next(a for a in parent_type.attach_points if a.name == block.attach_to.attach_point)
        held = holders.setdefault((block.attach_to.instance_id, point.name), [])
        if len(held) < point.capacity:
            held.append(block.instance_id)
            continue
        problems.append(
            Problem(
                code="overlap",
                message=f"{types[block.instance_id].label} {block.instance_id!r} is attached to the "
                f"{point.label.lower()} of {parent_type.label} {block.attach_to.instance_id!r}, which already holds "
                f"{', '.join(repr(h) for h in held)}. Remove one of them, or attach it elsewhere.",
                block=block.instance_id,
            )
        )
        blocked.add(block.instance_id)
    return problems


def _placement_problems(placed: PlacedBlock, is_root: bool) -> list[Problem]:
    placement = placed.instance.placement
    if is_root:
        if placement != Placement():
            return [
                Problem(
                    code="design_limit",
                    message=f"{placed.label} is the root of the assembly and sets the frame: it cannot be moved or "
                    "turned; set its placement to zero.",
                    block=placed.instance_id,
                )
            ]
        return []
    problems = []
    for name, angle in (
        ("mesiodistal", placement.axis_angles_deg.mesiodistal),
        ("buccolingual", placement.axis_angles_deg.buccolingual),
    ):
        if abs(angle) > MAX_AXIS_ANGLE_DEG:
            problems.append(
                Problem(
                    code="design_limit",
                    message=f"{placed.label}: the {name} angle is {angle:g}°; it must be within "
                    f"±{MAX_AXIS_ANGLE_DEG:g}°.",
                    block=placed.instance_id,
                )
            )
    return problems


def _walk(
    order: list[BlockInstance],
    types: Mapping[str, AnyBlockType],
    readings: Mapping[str, SettingsReading[Any]],
    blocked: set[str],
    root_frame: Transform,
    conventions: Conventions,
) -> dict[str, BlockContext[Any]]:
    """Contexts (without environment) of every placeable block, with the root at `root_frame`."""
    contexts: dict[str, BlockContext[Any]] = {}
    sockets: dict[str, dict[str, Socket]] = {}
    for block in order:
        reading = readings[block.instance_id]
        if reading.settings is None or block.instance_id in blocked:
            continue
        block_type = types[block.instance_id]
        if block.attach_to is None:
            ctx = block_type.context(block.instance_id, reading.settings, conventions=conventions, to_frame=root_frame)
        else:
            parent_sockets = sockets.get(block.attach_to.instance_id)
            if parent_sockets is None:
                continue
            socket = parent_sockets.get(block.attach_to.attach_point)
            if socket is None:
                continue
            ctx = block_type.context(
                block.instance_id,
                reading.settings,
                conventions=conventions,
                to_frame=socket.frame @ placement_transform(block.placement),
                parent=ParentLink(block.attach_to.instance_id, socket),
            )
        contexts[block.instance_id] = ctx
        sockets[block.instance_id] = {s.name: s for s in block_type.sockets(ctx)}
    return contexts


def environment_of(
    contexts: Mapping[str, BlockContext[Any]], types: Mapping[str, AnyBlockType]
) -> tuple[tuple[Volume, ...], tuple[KeepOut, ...]]:
    """All the bone and all the keep-out volumes the placed blocks provide."""
    bone: list[Volume] = []
    keep_outs: list[KeepOut] = []
    for instance_id, ctx in contexts.items():
        bone += types[instance_id].bone_volumes(ctx)
        keep_outs += types[instance_id].keep_out(ctx)
    return tuple(bone), tuple(keep_outs)


def place(scenario: Scenario, *, conventions: Conventions | None = None, blocks: Catalogue | None = None) -> Assembly:
    """Read every block's settings and place the assembly in the conventions frame.

    Refused (`validation_failed`) for an unknown block type or version, settings of the wrong kind or type, a
    scenario with no single root, or a block attached to a socket it cannot use. Design-limit problems are returned
    in `Assembly.problems`.
    """
    conventions = conventions or get_conventions()
    found = blocks or catalogue()
    types = {b.instance_id: found.for_instance(b) for b in scenario.blocks}
    order = _tree_order(scenario, types)
    for block in order:
        if block.attach_to is not None:
            _check_attachment(block, types[block.instance_id], types[block.attach_to.instance_id])
    readings = {
        b.instance_id: types[b.instance_id].read_settings(
            b.settings, instance_id=b.instance_id, conventions=conventions
        )
        for b in order
    }

    problems: list[Problem] = []
    blocked: set[str] = set()
    for index, block in enumerate(order):
        provisional = PlacedBlock(block, types[block.instance_id], readings[block.instance_id], None, None)
        problems += readings[block.instance_id].problems
        placement_problems = _placement_problems(provisional, is_root=index == 0)
        problems += placement_problems
        if placement_problems:
            blocked.add(block.instance_id)
    problems += _occupancy_problems(order, types, blocked)

    anatomical = _walk(order, types, readings, blocked, IDENTITY, conventions)
    reference = next(
        (
            b.instance_id
            for b in scenario.blocks
            if types[b.instance_id].is_axis_reference and b.instance_id in anatomical
        ),
        None,
    )
    frame = IDENTITY
    if reference is not None:
        frame = axis_alignment(anatomical[reference].to_frame.direction((0.0, 0.0, 1.0)))
    contexts = _walk(order, types, readings, blocked, frame, conventions)

    bone, keep_outs = environment_of(contexts, types)
    placed = []
    for block in order:
        ctx = contexts.get(block.instance_id)
        if ctx is not None:
            others = tuple(k for k in keep_outs if k.owner != block.instance_id)
            ctx = dataclasses.replace(ctx, environment=Environment(bone=bone, keep_outs=others))
        parent = block.attach_to.instance_id if block.attach_to is not None else None
        placed.append(PlacedBlock(block, types[block.instance_id], readings[block.instance_id], parent, ctx))
    return Assembly(scenario, tuple(placed), frame, reference, tuple(problems))
