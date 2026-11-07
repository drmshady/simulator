"""Pontic: a replacement tooth carried by its neighbours, not by bone (slice 1b; FR-004, FR-007, FR-011).

**Settings** (prosthesis group): mesiodistal and buccolingual widths, height (base to cusp tips), cusp height, the
occlusal shape (cusped, or a flat table), the clearance over the ridge and the material. The material's elastic
constants and strength reference come from the library.

**Placement and support.** A pontic sits at a site of the jaw segment (`site:<FDI>`), above the ridge with
`base_clearance_mm` between its base and the crest, where the mucosa would be (not modelled), so it never touches the
bone. Only connectors hold it: it offers `connector_mesial` and `connector_distal` sockets, at its contact areas,
where splinting connectors join it to the units beside it (a connector may hang from either unit). The shape follows
the crown's (`dentalsim.blocks._unit`).

**Check** `unsupported_pontic`: on each side, a connector must join the pontic to a unit that leads to a supported
one: a crown on its abutment, or a pontic that is itself held from its other side (two pontics side by side between
two crowns are held). Deleting an implant of a bridge leaves the pontic beside it unsupported, and says so.

**Occlusal points** as the crown's: `buccal_cusp`, `lingual_cusp`, `central_fossa`, `mesial_marginal_ridge`,
`distal_marginal_ridge` (on a flat table the cusp points lie on the table).
"""

from __future__ import annotations

from typing import Annotated

from dentalsim.blocks._unit import (
    DISTAL,
    MESIAL,
    OcclusalShape,
    UnitMaterial,
    UnitShape,
    side_name,
    unit_joints,
    unit_sockets,
)
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
from dentalsim.model.validity import Problem


class PonticSettings(BlockSettings):
    mesiodistal_mm: Annotated[
        float,
        Design(
            "prosthesis",
            "Mesiodistal width",
            unit="mm",
            minimum=4.0,
            maximum=14.0,
            site_width_offset_mm=-0.4,
            help="A new one starts 0.4 mm narrower than the mean width of its site's tooth.",
        ),
    ] = 7.0
    buccolingual_mm: Annotated[
        float, Design("prosthesis", "Buccolingual width", unit="mm", minimum=5.0, maximum=13.0)
    ] = 8.0
    height_mm: Annotated[
        float,
        Design("prosthesis", "Pontic height", unit="mm", minimum=5.0, maximum=12.0, help="Its base to the cusp tips."),
    ] = 7.5
    cusp_height_mm: Annotated[
        float,
        Design(
            "prosthesis",
            "Cusp height",
            unit="mm",
            minimum=0.5,
            maximum=3.5,
            help="A flat table lies this far below the height (the same body without its cusp ridges).",
        ),
    ] = 2.0
    occlusal_shape: Annotated[OcclusalShape, Design("prosthesis", "Occlusal shape")] = "cusped"
    base_clearance_mm: Annotated[
        float,
        Design(
            "prosthesis",
            "Clearance over the ridge",
            unit="mm",
            minimum=0.5,
            maximum=6.0,
            help="From the crest (at the implant platform level) to the pontic's base, where the mucosa would be.",
        ),
    ] = 2.0
    material: Annotated[UnitMaterial, Design("prosthesis", "Pontic material")] = "zirconia"
    E: Annotated[float, Library("prosthesis", "Pontic elastic modulus", "material.{material}.E", unit="MPa", above=0.0)]
    nu: Annotated[
        float,
        Library("prosthesis", "Pontic Poisson's ratio", "material.{material}.nu", unit="1", above=-1.0, below=0.5),
    ]
    strength: Annotated[
        float,
        Library(
            "prosthesis", "Pontic strength reference", "material.{material}.{material:strength}", unit="MPa", above=0.0
        ),
    ]

    def shape(self) -> UnitShape:
        return UnitShape.of(
            mesiodistal_mm=self.mesiodistal_mm,
            buccolingual_mm=self.buccolingual_mm,
            height_mm=self.height_mm,
            cusp_height_mm=self.cusp_height_mm,
            base_clearance_mm=self.base_clearance_mm,
            occlusal_shape=self.occlusal_shape,
        )


class Pontic(BlockType[PonticSettings]):
    type_id = "pontic"
    version = "1.0.0"
    slice = "1b"
    label = "Pontic"
    description = "A replacement tooth over the ridge, held by the connectors that join it to the units beside it."
    settings_model = PonticSettings
    attach_points = (
        AttachPoint("connector_mesial", "Mesial connector", "The mesial contact area, for a splinting connector."),
        AttachPoint("connector_distal", "Distal connector", "The distal contact area, for a splinting connector."),
    )
    attaches_to = ("site",)
    result_types = (ResultType("pontic_stress", "Pontic stress", regions=("pontic",)),)
    report_regions = ("pontic",)

    def rules(self, settings: PonticSettings, instance_id: str) -> list[Problem]:
        if settings.shape().cusps_too_high():
            return [
                self.design_problem(
                    instance_id,
                    f"Pontic {instance_id!r}: {settings.cusp_height_mm:g} mm cusps are too high for a "
                    f"{settings.height_mm:g} mm pontic; lower the cusps or raise the pontic.",
                )
            ]
        return []

    def check(self, ctx: BlockContext[PonticSettings]) -> list[Problem]:
        if ctx.parent is None or ctx.parent.socket.attach_point != "site":
            return []
        loose = [s for s in self.sockets(ctx) if not ctx.environment.supported_at(ctx.instance_id, s.name)]
        if not loose:
            return []
        if len(loose) == 2:
            where = ctx.to_frame.point((0.0, 0.0, ctx.settings.shape().contour_z))
            sides = "on either side"
        else:
            where = loose[0].frame.origin
            sides = f"on its {side_name(loose[0].name)} side"
        return [
            ctx.problem(
                "unsupported_pontic",
                f"Pontic {ctx.instance_id!r} is not joined to a supported unit {sides}: a pontic must be held on both "
                "sides. Join it with a splinting connector to a crown, or to a pontic held from its other side.",
                where,
            )
        ]

    # --- geometry -----------------------------------------------------------------------------------------------

    def build_geometry(self, ctx: BlockContext[PonticSettings]) -> Parts:
        s = ctx.settings
        ctx.parent_socket("site")
        body = s.shape().build()
        return Parts(
            parts=(Part("body", "pontic", s.material, s.E, s.nu, tuple(body), frozenset({"connector"}), "strength"),)
        )

    def occlusal_points(self, ctx: BlockContext[PonticSettings]) -> tuple[OcclusalPoint, ...]:
        return ctx.settings.shape().occlusal_points(ctx)

    def sockets(self, ctx: BlockContext[PonticSettings]) -> tuple[Socket, ...]:
        return unit_sockets(ctx, ctx.settings.shape(), (MESIAL, DISTAL))

    def joints(self, ctx: BlockContext[PonticSettings]) -> tuple[Joint, ...]:
        return unit_joints(ctx, f"{self.label} {ctx.instance_id!r}", ctx.settings.shape(), self.sockets(ctx))


BLOCK_TYPES = (Pontic(),)
