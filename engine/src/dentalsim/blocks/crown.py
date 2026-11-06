"""Crown on a crown seat (slice 1a; FR-007).

**Settings** (prosthesis group): crown height (margin to cusp tips), mesiodistal and buccolingual widths, cusp
height and material. The material's elastic constants and strength reference come from the library.

**Shape** (local frame: the crown seat, origin at the margin centre, +z along the seat axis, +x mesial, +y
buccal), generic rather than tooth-specific:

- a smooth body lofted through three sections: the margin circle (in the margin plane, which an angled abutment
  tilts), an ellipse of the full widths at the height of contour, and the occlusal table, an ellipse
  `TABLE_MD` × `TABLE_BL` of the widths at `height − cusp_height`;
- a buccal and a lingual cusp ridge on the table, ellipsoids running mesiodistally that meet in the central fossa;
- for a screw-retained crown, the access channel through the crown along the implant axis, sealed
  `SEAL_MM` below the occlusal table (the seal is taken as crown material).

Where the abutment post enters the body, the post displaces the crown (the abutment's `displaces`), so the crown's
inner surface fits the post exactly and is bonded to it; no cement layer is modelled.

**Occlusal points**: `buccal_cusp`, `lingual_cusp` (the ridge tops), `central_fossa` (where the ridges meet),
`mesial_marginal_ridge` and `distal_marginal_ridge` (on the table near its mesial and distal ends).

**Sockets** `connector_mesial` and `connector_distal` (for splinting connectors, slice 1b): at the mesial and
distal ends of the height of contour, +z pointing out of the crown, +y buccal (mesial is +x, or −x past the midline
of a full arch: the seat's `x_toward`). The same two contact areas are the
crown's **joints**, carried (the crown stands on its abutment), and where a connector enters the crown the crown
keeps its shape (it displaces the connector).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Annotated, Literal

from dentalsim.blocks.base import (
    AttachPoint,
    BlockContext,
    BlockSettings,
    BlockType,
    Design,
    Joint,
    Library,
    OcclusalPoint,
    Part,
    Parts,
    ResultType,
    Socket,
)
from dentalsim.geometry import occ as occ_tools
from dentalsim.geometry.shapes import Cylinder
from dentalsim.geometry.transform import Transform
from dentalsim.model.validity import Problem

TABLE_MD = 0.8
TABLE_BL = 0.65
CONTOUR_FRACTION = 0.4
"""Height of contour as a fraction of the occlusal-table height."""
CONTOUR_ABOVE_MARGIN_MM = 0.5
CUSP_SPREAD = 0.45
"""Cusp-ridge centres at ±CUSP_SPREAD of the table's buccolingual half-width."""
CUSP_WIDTH = 0.5
CUSP_LENGTH = 0.75
MARGINAL_RIDGE = 0.8
SEAL_MM = 1.5
MIN_OCCLUSAL_MM = 1.0
MIN_WALL_MM = 0.3
MIN_CUSP_CLEARANCE_MM = 0.3
POST_CLEARANCE_MM = 0.5
"""How far short of the abutment post a connector entering the crown stops."""


class CrownSettings(BlockSettings):
    height_mm: Annotated[
        float,
        Design("prosthesis", "Crown height", unit="mm", minimum=5.0, maximum=12.0, help="Margin to the cusp tips."),
    ] = 7.5
    mesiodistal_mm: Annotated[
        float, Design("prosthesis", "Mesiodistal width", unit="mm", minimum=5.0, maximum=14.0)
    ] = 10.5
    buccolingual_mm: Annotated[
        float, Design("prosthesis", "Buccolingual width", unit="mm", minimum=5.0, maximum=13.0)
    ] = 10.0
    cusp_height_mm: Annotated[float, Design("prosthesis", "Cusp height", unit="mm", minimum=0.5, maximum=3.5)] = 2.0
    material: Annotated[
        Literal["zirconia", "lithium_disilicate", "feldspathic", "composite", "cocr"],
        Design("prosthesis", "Crown material"),
    ] = "zirconia"
    E: Annotated[float, Library("prosthesis", "Crown elastic modulus", "material.{material}.E", unit="MPa", above=0.0)]
    nu: Annotated[
        float, Library("prosthesis", "Crown Poisson's ratio", "material.{material}.nu", unit="1", above=-1.0, below=0.5)
    ]
    strength: Annotated[
        float,
        Library(
            "prosthesis", "Crown strength reference", "material.{material}.{material:strength}", unit="MPa", above=0.0
        ),
    ]


@dataclass(frozen=True)
class CrownShape:
    """Dimensions derived from the settings and the seat's margin tilt, in mm."""

    half_md: float
    half_bl: float
    table_z: float
    table_md: float
    table_bl: float
    contour_z: float
    cusp_height: float

    @classmethod
    def of(cls, s: CrownSettings, margin_radius: float = 0.0, margin_tilt_deg: float = 0.0) -> CrownShape:
        table_z = s.height_mm - s.cusp_height_mm
        dip = margin_radius * math.sin(math.radians(margin_tilt_deg))
        return cls(
            half_md=s.mesiodistal_mm / 2,
            half_bl=s.buccolingual_mm / 2,
            table_z=table_z,
            table_md=TABLE_MD * s.mesiodistal_mm / 2,
            table_bl=TABLE_BL * s.buccolingual_mm / 2,
            contour_z=max(CONTOUR_FRACTION * table_z, dip + CONTOUR_ABOVE_MARGIN_MM),
            cusp_height=s.cusp_height_mm,
        )

    @property
    def cusp_centre_y(self) -> float:
        return CUSP_SPREAD * self.table_bl

    @property
    def cusp_semi_axes(self) -> tuple[float, float, float]:
        return (CUSP_LENGTH * self.table_md, CUSP_WIDTH * self.table_bl, self.cusp_height)

    @property
    def fossa_z(self) -> float:
        """Height where the two cusp ridges meet over the centre line."""
        ratio = self.cusp_centre_y / self.cusp_semi_axes[1]
        return self.table_z + self.cusp_height * math.sqrt(1.0 - ratio * ratio)


def _margin_normal(tilt_deg: float) -> tuple[float, float, float]:
    """The margin plane's normal in the seat frame: the seat's z turned by −tilt about x."""
    t = math.radians(tilt_deg)
    return (0.0, math.sin(t), math.cos(t))


class Crown(BlockType[CrownSettings]):
    type_id = "crown"
    version = "1.0.0"
    slice = "1a"
    label = "Crown"
    description = "A crown with a buccal and a lingual cusp ridge, on an abutment's crown seat."
    settings_model = CrownSettings
    attach_points = (
        AttachPoint("connector_mesial", "Mesial connector", "The mesial contact area, for a connector or pontic."),
        AttachPoint("connector_distal", "Distal connector", "The distal contact area, for a connector or pontic."),
    )
    attaches_to = ("crown_seat",)
    result_types = (ResultType("crown_stress", "Crown stress", regions=("crown",)),)
    report_regions = ("crown",)

    def rules(self, settings: CrownSettings, instance_id: str) -> list[Problem]:
        shape = CrownShape.of(settings)
        if shape.table_z - shape.cusp_height < shape.contour_z + MIN_CUSP_CLEARANCE_MM:
            return [
                self.design_problem(
                    instance_id,
                    f"Crown {instance_id!r}: {settings.cusp_height_mm:g} mm cusps are too high for a "
                    f"{settings.height_mm:g} mm crown; lower the cusps or raise the crown.",
                )
            ]
        return []

    def check(self, ctx: BlockContext[CrownSettings]) -> list[Problem]:
        if ctx.parent is None or ctx.parent.socket.name != "crown_seat":
            return []
        s, seat = ctx.settings, ctx.parent.socket
        margin = seat.number("margin_radius_mm")
        shape = CrownShape.of(s, margin, seat.number("margin_tilt_deg"))
        core_height, core_top = seat.number("core_height_mm"), seat.number("core_top_radius_mm")
        where = ctx.to_frame.point((0.0, 0.0, shape.table_z))
        messages = []
        clearance = shape.table_z - core_height
        if clearance < MIN_OCCLUSAL_MM:
            needed = core_height + MIN_OCCLUSAL_MM + s.cusp_height_mm
            messages.append(
                f"it leaves {clearance:.1f} mm over the abutment post (at least {MIN_OCCLUSAL_MM:g} mm); raise the "
                f"crown to at least {needed:.1f} mm or lower the post"
            )
        if min(shape.half_md, shape.half_bl) < margin + MIN_WALL_MM:
            messages.append(
                f"it is narrower than the abutment margin; both widths must be at least "
                f"{2 * (margin + MIN_WALL_MM):.1f} mm"
            )
        if min(shape.table_md, shape.table_bl) < core_top + MIN_WALL_MM:
            messages.append("its occlusal table is narrower than the top of the abutment post; widen the crown")
        if shape.table_z - shape.cusp_height < shape.contour_z + MIN_CUSP_CLEARANCE_MM:
            messages.append("its cusps are too high for its height over this tilted margin")
        return [ctx.problem("design_limit", f"Crown {ctx.instance_id!r}: {m}.", where) for m in messages]

    # --- geometry -----------------------------------------------------------------------------------------------

    def build_geometry(self, ctx: BlockContext[CrownSettings]) -> Parts:
        s, seat = ctx.settings, ctx.parent_socket("crown_seat")
        margin, tilt = seat.number("margin_radius_mm"), seat.number("margin_tilt_deg")
        shape = CrownShape.of(s, margin, tilt)
        normal = _margin_normal(tilt)
        body = occ_tools.loft(
            [
                occ_tools.circle_loop((0.0, 0.0, 0.0), margin, normal),
                occ_tools.ellipse_loop((0.0, 0.0, shape.contour_z), shape.half_md, shape.half_bl),
                occ_tools.ellipse_loop((0.0, 0.0, shape.table_z), shape.table_md, shape.table_bl),
            ]
        )
        cusps = [
            occ_tools.ellipsoid((0.0, side * shape.cusp_centre_y, shape.table_z), shape.cusp_semi_axes)
            for side in (1.0, -1.0)
        ]
        crown = occ_tools.fuse([*body, *cusps])
        channel_radius = seat.number("channel_radius_mm")
        if seat.text("retention") == "screw" and channel_radius > 0:
            reach = (shape.table_z - SEAL_MM) / normal[2]
            start = tuple(-5.0 * c for c in normal)
            end = tuple(reach * c for c in normal)
            channel = Cylinder((start[0], start[1], start[2]), (end[0], end[1], end[2]), channel_radius)
            crown = occ_tools.cut(crown, channel.build_occ())
        part = Part("body", "crown", s.material, s.E, s.nu, tuple(crown), frozenset({"connector"}), "strength")
        return Parts(parts=(part,))

    def occlusal_points(self, ctx: BlockContext[CrownSettings]) -> tuple[OcclusalPoint, ...]:
        shape = self._shape(ctx)
        top = shape.table_z + shape.cusp_height
        ridge = ctx.mesial_sign * MARGINAL_RIDGE * shape.table_md
        local = (
            ("buccal_cusp", "Buccal cusp", (0.0, shape.cusp_centre_y, top)),
            ("lingual_cusp", "Lingual cusp", (0.0, -shape.cusp_centre_y, top)),
            ("central_fossa", "Central fossa", (0.0, 0.0, shape.fossa_z)),
            ("mesial_marginal_ridge", "Mesial marginal ridge", (ridge, 0.0, shape.table_z)),
            ("distal_marginal_ridge", "Distal marginal ridge", (-ridge, 0.0, shape.table_z)),
        )
        up = ctx.to_frame.direction((0.0, 0.0, 1.0))
        return tuple(OcclusalPoint(name, label, ctx.to_frame.point(p), up) for name, label, p in local)

    def sockets(self, ctx: BlockContext[CrownSettings]) -> tuple[Socket, ...]:
        shape = self._shape(ctx)
        plus = Transform.from_axes((shape.half_md, 0.0, shape.contour_z), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0))
        minus = Transform.from_axes((-shape.half_md, 0.0, shape.contour_z), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0))
        mesial, distal = (plus, minus) if ctx.mesial_sign > 0 else (minus, plus)
        return (ctx.socket("connector_mesial", mesial), ctx.socket("connector_distal", distal))

    def joints(self, ctx: BlockContext[CrownSettings]) -> tuple[Joint, ...]:
        """The contact areas, carried, with the room a connector has there: down to the margin, up to the occlusal
        table, and inward until `POST_CLEARANCE_MM` short of the abutment post."""
        shape = self._shape(ctx)
        depth = 0.0
        if ctx.parent is not None and ctx.parent.socket.name == "crown_seat":
            seat = ctx.parent.socket
            base, top = seat.number("core_base_radius_mm"), seat.number("core_top_radius_mm")
            post = base + (top - base) * min(shape.contour_z / seat.number("core_height_mm"), 1.0)
            depth = max(shape.half_md - post - POST_CLEARANCE_MM, 0.0)
        label = f"{self.label} {ctx.instance_id!r}"
        up = ctx.to_frame.direction((0.0, 0.0, 1.0))
        return tuple(
            Joint(
                ctx.instance_id,
                label,
                socket.name,
                socket.frame.origin,
                socket.frame.axes[2],
                carried=True,
                occlusal=up,
                half_widths_mm=(shape.half_md, shape.half_bl),
                max_depth_mm=depth,
                room_below_mm=shape.contour_z,
                room_above_mm=shape.table_z - shape.contour_z,
            )
            for socket in self.sockets(ctx)
        )

    def _shape(self, ctx: BlockContext[CrownSettings]) -> CrownShape:
        if ctx.parent is None or ctx.parent.socket.name != "crown_seat":
            return CrownShape.of(ctx.settings)
        seat = ctx.parent.socket
        return CrownShape.of(ctx.settings, seat.number("margin_radius_mm"), seat.number("margin_tilt_deg"))


BLOCK_TYPES = (Crown(),)
