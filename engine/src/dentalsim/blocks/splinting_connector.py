"""Splinting connector: the joint between two neighbouring units of a fixed prosthesis (slice 1b; FR-004, FR-007).

**Settings** (prosthesis group): the cross-section, height (occlusogingival) and width (buccolingual), and the
material. The material's elastic constants and strength reference come from the library.

**Placement.** It hangs from a unit's contact area (a crown's, a pontic's or a cantilever extension's
`connector_mesial` or `connector_distal` socket) and reaches the facing contact area of the unit beside it: the
nearest joint of another unit lying ahead of the socket (along its +z, out of the unit) by at most `MAX_GAP_MM`,
within `MAX_OFFSET_MM` of its axis, and facing back toward it. It does not need to be told which unit that is, so
it follows when a unit is moved or resized; it reports the two joints it joins as a `Link`, which is what tells a
pontic it is held.

**Shape** (local frame: the socket, +z out of the unit it hangs from, +y buccal): a bar of elliptical cross-section
(`height_mm` occlusogingivally, `width_mm` buccolingually) along the line between the two contact areas, reaching into
each unit. Where it enters a unit, the unit keeps its shape (the units displace the connector), so the connector is
the neck between them, bonded to both. Each unit's joint says how much room it has, and the bar uses it:

- it reaches far enough in that the rim of its end lies `RIM_INSIDE_MM` inside the unit's side (at least `EMBED_MM`),
  but no farther than the unit allows (short of an abutment post, or of the unit's middle); where that is too far,
  its rim stands `RIM_INSIDE_MM` out of the unit's side instead;
- its section is lifted, or lowered, so that it stays `ROOM_MARGIN_MM` clear of the units' margins or bases and of
  their occlusal tables.

Without these, the end of the bar or its lowest line could graze a unit's surface or a crown's margin within
hundredths of a millimetre, which meshes into badly shaped elements. One that reaches no unit is built as a stub
`STUB_MM` long, so the builder can show it.

**Checks** `unsupported_pontic`: a connector that reaches no unit; `overlap`: two connectors joining the same two
contact areas; `design_limit`: a section taller than the units it joins leave room for.
"""

from __future__ import annotations

import math
from typing import Annotated

import numpy as np

from dentalsim.blocks._unit import UnitMaterial, side_name
from dentalsim.blocks.base import (
    BlockContext,
    BlockSettings,
    BlockType,
    Design,
    Joint,
    Library,
    Link,
    Part,
    Parts,
    RefinementHint,
    ResultType,
)
from dentalsim.geometry import occ as occ_tools
from dentalsim.geometry.transform import FloatArray, Point, Transform
from dentalsim.model.validity import Problem

MAX_GAP_MM = 3.0
"""How far ahead of its socket a connector looks for the facing contact area."""
MAX_OFFSET_MM = 2.0
"""How far off the socket's axis the facing contact area may lie (units of different heights, a tilted implant)."""
MIN_FACING = 0.7
"""The facing contact area's outward normal must point back along the axis by at least this cosine."""
EMBED_MM = 0.6
"""How far the bar reaches into each unit at least, when the unit has room."""
MIN_EMBED_MM = 0.3
JOIN_FEATURE_MM = 1.0
"""The feature size its refinement hint resolves: where the bar meets a unit's curved side, the mesh must follow
sub-millimetre edges (about 1 mm elements at the preview level, 0.5 mm at the standard level)."""
RIM_INSIDE_MM = 0.3
ROOM_MARGIN_MM = 0.5
STUB_MM = 1.0
SEAM_DEG = 45.0
"""Where the bar's seam runs: off its top, bottom and sides, which meet the units' surfaces (seams that cross
other faces there leave micrometre-long edges, and badly shaped elements)."""
CONNECTOR_SOCKETS = ("connector_mesial", "connector_distal")


class SplintingConnectorSettings(BlockSettings):
    height_mm: Annotated[
        float,
        Design(
            "prosthesis",
            "Connector height",
            unit="mm",
            minimum=2.0,
            maximum=6.0,
            help="Occlusogingival height of its cross-section.",
        ),
    ] = 4.0
    width_mm: Annotated[
        float,
        Design(
            "prosthesis",
            "Connector width",
            unit="mm",
            minimum=2.0,
            maximum=7.0,
            help="Buccolingual width of its section.",
        ),
    ] = 5.0
    material: Annotated[UnitMaterial, Design("prosthesis", "Connector material")] = "zirconia"
    E: Annotated[
        float, Library("prosthesis", "Connector elastic modulus", "material.{material}.E", unit="MPa", above=0.0)
    ]
    nu: Annotated[
        float,
        Library("prosthesis", "Connector Poisson's ratio", "material.{material}.nu", unit="1", above=-1.0, below=0.5),
    ]
    strength: Annotated[
        float,
        Library(
            "prosthesis",
            "Connector strength reference",
            "material.{material}.{material:strength}",
            unit="MPa",
            above=0.0,
        ),
    ]

    @property
    def area_mm2(self) -> float:
        return math.pi * self.height_mm * self.width_mm / 4


def elliptic_bar(start: Point, end: Point, across: float, wide: float, across_hint: Point) -> list[int]:
    """A bar of elliptical cross-section from `start` to `end`: semi-axes `across / 2` along `across_hint` (made
    square to the bar) and `wide / 2` square to both. An exact ellipse extruded, then placed rigidly."""
    a, b = np.asarray(start, dtype=np.float64), np.asarray(end, dtype=np.float64)
    length = float(np.linalg.norm(b - a))
    ez = (b - a) / length
    hint = np.asarray(across_hint, dtype=np.float64)
    ex = hint - (hint @ ez) * ez
    if np.linalg.norm(ex) < 1e-6:
        ex = np.cross(ez, (0.0, 1.0, 0.0))
    ex /= np.linalg.norm(ex)
    kernel = occ_tools.occ()
    face = kernel.addPlaneSurface([occ_tools.ellipse_loop((0.0, 0.0, 0.0), across / 2, wide / 2, SEAM_DEG)])
    tags = [tag for dim, tag in kernel.extrude([(2, face)], 0.0, 0.0, length) if dim == 3]
    frame = Transform.from_axes(a, ex, np.cross(ez, ex))
    occ_tools.transform_volumes(tags, frame)
    return tags


class SplintingConnector(BlockType[SplintingConnectorSettings]):
    type_id = "splinting_connector"
    version = "1.0.0"
    slice = "1b"
    label = "Splinting connector"
    description = "The joint between two neighbouring units: an elliptical neck from one contact area to the next."
    settings_model = SplintingConnectorSettings
    attaches_to = CONNECTOR_SOCKETS
    result_types = (ResultType("connector_stress", "Connector stress", regions=("connector",)),)
    report_regions = ("connector",)

    def partner(self, ctx: BlockContext[SplintingConnectorSettings]) -> Joint | None:
        """The facing contact area of the unit beside the one it hangs from, if any (see the module notes)."""
        if ctx.parent is None:
            return None
        origin, axis = np.asarray(ctx.to_frame.origin), np.asarray(ctx.to_frame.axes[2])
        best: tuple[float, Joint] | None = None
        for joint in ctx.environment.joints:
            if joint.owner == ctx.parent.instance_id:
                continue
            offset = np.asarray(joint.position_mm) - origin
            along = float(offset @ axis)
            lateral = float(np.linalg.norm(offset - along * axis))
            facing = float(np.asarray(joint.normal) @ axis)
            if not (-EMBED_MM <= along <= MAX_GAP_MM and lateral <= MAX_OFFSET_MM and facing <= -MIN_FACING):
                continue
            distance = float(np.linalg.norm(offset))
            if best is None or distance < best[0]:
                best = (distance, joint)
        return None if best is None else best[1]

    def _joints(self, ctx: BlockContext[SplintingConnectorSettings]) -> tuple[Joint | None, Joint | None]:
        """The joint it hangs from and the one it reaches (None when unknown)."""
        if ctx.parent is None:
            return None, None
        return ctx.environment.joint(ctx.parent.instance_id, ctx.parent.socket.name), self.partner(ctx)

    def _lift(self, ctx: BlockContext[SplintingConnectorSettings], joints: tuple[Joint | None, ...]) -> float:
        """How far its section moves occlusally from the contact areas' line to stay clear of the units' margins or
        bases and tables; negative moves it down."""
        known = [j for j in joints if j is not None]
        half = ctx.settings.height_mm / 2
        below = min((j.room_below_mm for j in known), default=math.inf) - ROOM_MARGIN_MM
        above = min((j.room_above_mm for j in known), default=math.inf) - ROOM_MARGIN_MM
        if half > below:
            return min(half - below, max(above - half, 0.0))
        if half > above:
            return -min(half - above, max(below - half, 0.0))
        return 0.0

    def links(self, ctx: BlockContext[SplintingConnectorSettings]) -> tuple[Link, ...]:
        found = self.partner(ctx)
        if ctx.parent is None or found is None:
            return ()
        return (Link(ctx.instance_id, (ctx.parent.instance_id, ctx.parent.socket.name), (found.owner, found.socket)),)

    def check(self, ctx: BlockContext[SplintingConnectorSettings]) -> list[Problem]:
        if ctx.parent is None:
            return []
        here = (ctx.parent.instance_id, ctx.parent.socket.name)
        unit = ctx.environment.joint(*here)
        name = unit.label if unit is not None else repr(ctx.parent.instance_id)
        side = side_name(ctx.parent.socket.name)
        mine = next((link for link in ctx.environment.links if link.by == ctx.instance_id), None)
        if mine is None:
            return [
                ctx.problem(
                    "unsupported_pontic",
                    f"Splinting connector {ctx.instance_id!r} on the {side} side of {name} reaches no unit within "
                    f"{MAX_GAP_MM:g} mm. Remove it, or place a unit beside it there.",
                    ctx.to_frame.origin,
                )
            ]
        room = [
            j.room_below_mm + j.room_above_mm - 2 * ROOM_MARGIN_MM for j in (unit, ctx.environment.joint(*mine.b)) if j
        ]
        if room and ctx.settings.height_mm > min(room):
            return [
                ctx.problem(
                    "design_limit",
                    f"Splinting connector {ctx.instance_id!r} is {ctx.settings.height_mm:g} mm high, but the units it "
                    f"joins leave room for at most {max(min(room), 0.0):.1f} mm between their margins or bases and "
                    "their occlusal tables; make it lower.",
                    ctx.to_frame.origin,
                )
            ]
        pair = {mine.a, mine.b}
        twins = sorted(
            link.by for link in ctx.environment.links if link.by != ctx.instance_id and {link.a, link.b} == pair
        )
        if twins and twins[0] < ctx.instance_id:
            other = ctx.environment.joint(*mine.b)
            return [
                ctx.problem(
                    "overlap",
                    f"Splinting connectors {twins[0]!r} and {ctx.instance_id!r} both join {name} to "
                    f"{other.label if other is not None else mine.b[0]!r}; remove one of them.",
                    ctx.to_frame.origin,
                )
            ]
        return []

    # --- geometry -----------------------------------------------------------------------------------------------

    def build_geometry(self, ctx: BlockContext[SplintingConnectorSettings]) -> Parts:
        s = ctx.settings
        ctx.parent_socket(*CONNECTOR_SOCKETS)
        here, found = self._joints(ctx)
        local = ctx.to_frame.inverse()
        reach = np.array([0.0, 0.0, STUB_MM])
        if found is not None:
            reach = np.asarray(local.point(found.position_mm))
        length = float(np.linalg.norm(reach))
        direction = reach / length if length > 1e-3 else np.array([0.0, 0.0, 1.0])
        up = np.asarray(local.direction(here.occlusal if here is not None else ctx.to_frame.axes[0]))
        lift = self._lift(ctx, (here, found)) * (up - (up @ direction) * direction)
        start = lift - self._embed(s, here) * direction
        end = reach + lift + self._embed(s, found) * direction
        bar = elliptic_bar(_point(start), _point(end), s.height_mm, s.width_mm, _point(up))
        return Parts(
            parts=(Part("body", "connector", s.material, s.E, s.nu, tuple(bar), strength_setting="strength"),),
            hints=(RefinementHint("neck", "body", feature_mm=JOIN_FEATURE_MM, extent_mm=1.5),),
        )

    @staticmethod
    def _embed(s: SplintingConnectorSettings, joint: Joint | None) -> float:
        """How far the bar reaches into a unit: its end's rim `RIM_INSIDE_MM` inside the unit's side, within what the
        unit allows (see the module notes)."""
        if joint is None or min(joint.half_widths_mm) <= 0:
            return EMBED_MM
        along, across = joint.half_widths_mm
        ratio = min(s.width_mm / 2 / across, 1.0)
        rim = along * (1.0 - math.sqrt(1.0 - ratio * ratio))  # how far the unit's side recedes at the bar's rim
        allowed = joint.max_depth_mm if joint.max_depth_mm > 0 else math.inf
        inside, outside = rim + RIM_INSIDE_MM, rim - RIM_INSIDE_MM
        if inside <= allowed:
            return max(inside, EMBED_MM) if allowed >= EMBED_MM else inside
        if MIN_EMBED_MM <= outside <= allowed:
            return outside  # the rim stands clearly out of the unit's side, not grazing it
        # Neither fits: the depth, between the least and the most allowed, that keeps the rim farthest from the side.
        return max((min(MIN_EMBED_MM, allowed), allowed), key=lambda depth: abs(rim - depth))


def _point(values: FloatArray) -> Point:
    return (float(values[0]), float(values[1]), float(values[2]))


BLOCK_TYPES = (SplintingConnector(),)
