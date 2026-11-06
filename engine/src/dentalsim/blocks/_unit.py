"""Shared shape of the free-standing prosthesis units, the pontic and the cantilever extension (slice 1b).

Not a block module: the registry skips modules whose name starts with `_`.

A unit sits at a site of the jaw segment (local frame: origin at the site's crest centre, at the implant platform
level; x along the arch, y buccal, z occlusal) but rests on nothing there: a gap of `base_clearance_mm` separates its
base from the crest, where the mucosa would be (not modelled), and only the connectors joining it to its neighbours
hold it. Its shape follows the crown's (`dentalsim.blocks.crown`), so that units side by side line up: a body lofted
through three ellipses, the base (`BASE_MD` × `BASE_BL` of the widths, a rounded ridge-lap), the height of contour
(the full widths) and the occlusal table (`TABLE_MD` × `TABLE_BL` of the widths), with a buccal and a lingual cusp
ridge on the table (cusped), or the same body without them (flat). With the template defaults (2 mm clearance, as
the abutment collar), its contact areas and occlusal table are at the same heights as the crowns beside it. The
lofts' seams run on the buccal side (`SEAM_DEG`), away from the contact areas.

The contact areas, at the mesial and distal ends of the height of contour, are its connector sockets and its joints
(not carried); past the midline of a full arch, where the site's +x points distal, the mesial one is on the −x end.
`centre_x_mm` moves the body along the arch from the site's centre (the cantilever extension keeps its joined end at
the site's edge).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

from dentalsim.blocks.base import BlockContext, Joint, OcclusalPoint, Socket
from dentalsim.blocks.crown import (
    CONTOUR_ABOVE_MARGIN_MM,
    CONTOUR_FRACTION,
    CUSP_LENGTH,
    MARGINAL_RIDGE,
    MIN_CUSP_CLEARANCE_MM,
    TABLE_BL,
    TABLE_MD,
)
from dentalsim.geometry import occ as occ_tools
from dentalsim.geometry.transform import Transform

OcclusalShape = Literal["cusped", "flat"]
UnitMaterial = Literal["zirconia", "lithium_disilicate", "feldspathic", "composite", "cocr"]

BASE_MD = 0.6
BASE_BL = 0.6
"""The base ellipse as fractions of the widths."""

MESIAL, DISTAL = "connector_mesial", "connector_distal"
CENTRE_CLEARANCE_MM = 0.5
UNIT_CUSP_SPREAD = 0.4
UNIT_CUSP_WIDTH = 0.45
"""The cusp ridges of a unit, as fractions of the table's buccolingual half-width: a little narrower and nearer the
middle than the crown's (`CUSP_SPREAD`, `CUSP_WIDTH`), so they end 0.15 of the half-width short of the table's edge
instead of 0.05, which meshed into badly shaped elements; they still meet in the central fossa."""
SEAM_DEG = 90.0
"""Where the lofted body's seam runs: on its buccal side, away from the contact areas where connectors enter it."""


@dataclass(frozen=True)
class UnitShape:
    """Dimensions of a unit in its local frame, in mm."""

    centre_x: float
    half_md: float
    half_bl: float
    base_z: float
    contour_z: float
    table_z: float
    cusp_height: float
    cusped: bool

    @classmethod
    def of(
        cls,
        *,
        mesiodistal_mm: float,
        buccolingual_mm: float,
        height_mm: float,
        cusp_height_mm: float,
        base_clearance_mm: float,
        occlusal_shape: OcclusalShape,
        centre_x_mm: float = 0.0,
    ) -> UnitShape:
        cusped = occlusal_shape == "cusped"
        cusp = cusp_height_mm if cusped else 0.0
        rise = height_mm - cusp_height_mm  # a flat table lies where the cusped one would
        return cls(
            centre_x=centre_x_mm,
            half_md=mesiodistal_mm / 2,
            half_bl=buccolingual_mm / 2,
            base_z=base_clearance_mm,
            contour_z=base_clearance_mm + max(CONTOUR_FRACTION * rise, CONTOUR_ABOVE_MARGIN_MM),
            table_z=base_clearance_mm + rise,
            cusp_height=cusp,
            cusped=cusped,
        )

    @property
    def table_md(self) -> float:
        return TABLE_MD * self.half_md

    @property
    def table_bl(self) -> float:
        return TABLE_BL * self.half_bl

    @property
    def cusp_centre_y(self) -> float:
        return UNIT_CUSP_SPREAD * self.table_bl

    @property
    def cusp_semi_axes(self) -> tuple[float, float, float]:
        return (CUSP_LENGTH * self.table_md, UNIT_CUSP_WIDTH * self.table_bl, self.cusp_height)

    @property
    def fossa_z(self) -> float:
        if not self.cusped:
            return self.table_z
        ratio = self.cusp_centre_y / self.cusp_semi_axes[1]
        return self.table_z + self.cusp_height * math.sqrt(1.0 - ratio * ratio)

    def cusps_too_high(self) -> bool:
        return self.cusped and self.table_z - self.cusp_height < self.contour_z + MIN_CUSP_CLEARANCE_MM

    # --- geometry, points and sockets ---------------------------------------------------------------------------

    def build(self) -> list[int]:
        """The unit's solid in the current gmsh model, in the local frame."""
        x = self.centre_x
        body = occ_tools.loft(
            [
                occ_tools.ellipse_loop((x, 0.0, self.base_z), BASE_MD * self.half_md, BASE_BL * self.half_bl, SEAM_DEG),
                occ_tools.ellipse_loop((x, 0.0, self.contour_z), self.half_md, self.half_bl, SEAM_DEG),
                occ_tools.ellipse_loop((x, 0.0, self.table_z), self.table_md, self.table_bl, SEAM_DEG),
            ]
        )
        if not self.cusped:
            return body
        cusps = [
            occ_tools.ellipsoid((x, side * self.cusp_centre_y, self.table_z), self.cusp_semi_axes)
            for side in (1.0, -1.0)
        ]
        return occ_tools.fuse([*body, *cusps])

    def occlusal_points(self, ctx: BlockContext[Any]) -> tuple[OcclusalPoint, ...]:
        x, top = self.centre_x, self.table_z + self.cusp_height
        ridge = ctx.mesial_sign * MARGINAL_RIDGE * self.table_md
        local = (
            ("buccal_cusp", "Buccal cusp", (x, self.cusp_centre_y, top)),
            ("lingual_cusp", "Lingual cusp", (x, -self.cusp_centre_y, top)),
            ("central_fossa", "Central fossa", (x, 0.0, self.fossa_z)),
            ("mesial_marginal_ridge", "Mesial marginal ridge", (x + ridge, 0.0, self.table_z)),
            ("distal_marginal_ridge", "Distal marginal ridge", (x - ridge, 0.0, self.table_z)),
        )
        up = ctx.to_frame.direction((0.0, 0.0, 1.0))
        return tuple(OcclusalPoint(name, label, ctx.to_frame.point(p), up) for name, label, p in local)

    def socket_frames(self, mesial_sign: float = 1.0) -> dict[str, Transform]:
        """The connector sockets at the contact areas, +z out of the unit, +y buccal (as the crown's); the mesial
        one is on the +x end, or the −x end when `mesial_sign` is −1 (past the midline of a full arch)."""
        x = self.centre_x
        plus = Transform.from_axes((x + self.half_md, 0.0, self.contour_z), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0))
        minus = Transform.from_axes((x - self.half_md, 0.0, self.contour_z), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0))
        return {MESIAL: plus, DISTAL: minus} if mesial_sign > 0 else {MESIAL: minus, DISTAL: plus}


def unit_sockets(ctx: BlockContext[Any], shape: UnitShape, names: tuple[str, ...]) -> tuple[Socket, ...]:
    frames = shape.socket_frames(ctx.mesial_sign)
    return tuple(ctx.socket(name, frames[name]) for name in names)


def unit_joints(ctx: BlockContext[Any], label: str, shape: UnitShape, sockets: tuple[Socket, ...]) -> tuple[Joint, ...]:
    """The contact areas, not carried, with the room a connector has there: down to the base, up to the table, and
    inward until `CENTRE_CLEARANCE_MM` short of the unit's middle (the connector on its other side reaches there)."""
    up = ctx.to_frame.direction((0.0, 0.0, 1.0))
    return tuple(
        Joint(
            ctx.instance_id,
            label,
            s.name,
            s.frame.origin,
            s.frame.axes[2],
            carried=False,
            occlusal=up,
            half_widths_mm=(shape.half_md, shape.half_bl),
            max_depth_mm=max(shape.half_md - CENTRE_CLEARANCE_MM, 0.0),
            room_below_mm=shape.contour_z - shape.base_z,
            room_above_mm=shape.table_z - shape.contour_z,
        )
        for s in sockets
    )


def side_name(socket: str) -> str:
    return "mesial" if socket == MESIAL else "distal"
