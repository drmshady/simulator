"""Cantilever extension: a unit held at one end only, by the unit it is joined to (slice 1b; FR-004, FR-007).

**Settings** (prosthesis group): which end is free (distal or mesial), the cantilever length, the buccolingual width,
height (base to cusp tips), cusp height, occlusal shape, clearance over the ridge and material. The material's
elastic constants and strength reference come from the library. The cantilever length is a design limit: at most
`MAX_LENGTH_MM`.

**Placement and support.** Like a pontic (`dentalsim.blocks.pontic`), it sits at a site of the jaw segment, above the
ridge, and rests on nothing there. Its joined end lies at the edge of its site toward the unit it hangs from, and it
reaches `length_mm` along the arch toward its free end. The joined end is its one connector socket and joint
(`connector_mesial` when the free end is distal); a splinting connector there joins it to the end of a supported unit.
On the far side of the midline the names mesial and distal follow the jaw segment's sites (see there).

**Check** `unsupported_pontic`: its joined end must be held by a supported unit (a crown, or a pontic held on both
sides). Deleting the implant under the unit it hangs from leaves it unsupported, and says so.
"""

from __future__ import annotations

from typing import Annotated, Literal

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

MAX_LENGTH_MM = 15.0
"""The longest cantilever the block builds (a design limit for the owner to review, not a clinical recommendation)."""


class CantileverExtensionSettings(BlockSettings):
    free_end: Annotated[
        Literal["distal", "mesial"],
        Design("prosthesis", "Free end", help="The end away from the unit it is joined to."),
    ] = "distal"
    length_mm: Annotated[
        float,
        Design(
            "prosthesis",
            "Cantilever length",
            unit="mm",
            minimum=4.0,
            maximum=MAX_LENGTH_MM,
            help="From its joined end, at the edge of its site, to its free end.",
        ),
    ] = 10.0
    buccolingual_mm: Annotated[
        float, Design("prosthesis", "Buccolingual width", unit="mm", minimum=5.0, maximum=13.0)
    ] = 10.0
    height_mm: Annotated[
        float,
        Design("prosthesis", "Unit height", unit="mm", minimum=5.0, maximum=12.0, help="Its base to the cusp tips."),
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
            help="From the crest (at the implant platform level) to the unit's base, where the mucosa would be.",
        ),
    ] = 2.0
    material: Annotated[UnitMaterial, Design("prosthesis", "Material")] = "zirconia"
    E: Annotated[float, Library("prosthesis", "Elastic modulus", "material.{material}.E", unit="MPa", above=0.0)]
    nu: Annotated[
        float, Library("prosthesis", "Poisson's ratio", "material.{material}.nu", unit="1", above=-1.0, below=0.5)
    ]
    strength: Annotated[
        float,
        Library("prosthesis", "Strength reference", "material.{material}.{material:strength}", unit="MPa", above=0.0),
    ]

    @property
    def joined_socket(self) -> str:
        return MESIAL if self.free_end == "distal" else DISTAL

    def shape(self, site_width_mm: float | None = None, mesial_sign: float = 1.0) -> UnitShape:
        """The unit's shape: its joined end at the edge of a site `site_width_mm` wide (centred when not given), its
        free end toward local −x when distal (+x past the midline of a full arch, where `mesial_sign` is −1)."""
        toward_free = -mesial_sign if self.free_end == "distal" else mesial_sign
        centre = 0.0 if site_width_mm is None else toward_free * (self.length_mm - site_width_mm) / 2
        return UnitShape.of(
            mesiodistal_mm=self.length_mm,
            buccolingual_mm=self.buccolingual_mm,
            height_mm=self.height_mm,
            cusp_height_mm=self.cusp_height_mm,
            base_clearance_mm=self.base_clearance_mm,
            occlusal_shape=self.occlusal_shape,
            centre_x_mm=centre,
        )


class CantileverExtension(BlockType[CantileverExtensionSettings]):
    type_id = "cantilever_extension"
    version = "1.0.0"
    slice = "1b"
    label = "Cantilever extension"
    description = "A unit over the ridge held at one end only, by a connector to the end of a supported unit."
    settings_model = CantileverExtensionSettings
    attach_points = (
        AttachPoint("connector_mesial", "Mesial connector", "Its joined end when the free end is distal."),
        AttachPoint("connector_distal", "Distal connector", "Its joined end when the free end is mesial."),
    )
    attaches_to = ("site",)
    result_types = (ResultType("cantilever_stress", "Cantilever stress", regions=("cantilever",)),)
    report_regions = ("cantilever",)

    def rules(self, settings: CantileverExtensionSettings, instance_id: str) -> list[Problem]:
        if settings.shape().cusps_too_high():
            return [
                self.design_problem(
                    instance_id,
                    f"Cantilever extension {instance_id!r}: {settings.cusp_height_mm:g} mm cusps are too high for a "
                    f"{settings.height_mm:g} mm unit; lower the cusps or raise the unit.",
                )
            ]
        return []

    def check(self, ctx: BlockContext[CantileverExtensionSettings]) -> list[Problem]:
        if ctx.parent is None or ctx.parent.socket.attach_point != "site":
            return []
        joined = ctx.settings.joined_socket
        if ctx.environment.supported_at(ctx.instance_id, joined):
            return []
        (socket,) = self.sockets(ctx)
        return [
            ctx.problem(
                "unsupported_pontic",
                f"Cantilever extension {ctx.instance_id!r} is not joined to a supported unit at its "
                f"{side_name(joined)} end. Join it with a splinting connector to a crown, or to a pontic held on both "
                "sides.",
                socket.frame.origin,
            )
        ]

    # --- geometry -----------------------------------------------------------------------------------------------

    def _shape(self, ctx: BlockContext[CantileverExtensionSettings]) -> UnitShape:
        parent = ctx.parent.socket if ctx.parent is not None else None
        width = parent.number("tooth_width_mm") if parent is not None and parent.attach_point == "site" else None
        return ctx.settings.shape(width, ctx.mesial_sign)

    def build_geometry(self, ctx: BlockContext[CantileverExtensionSettings]) -> Parts:
        s = ctx.settings
        ctx.parent_socket("site")
        body = self._shape(ctx).build()
        part = Part("body", "cantilever", s.material, s.E, s.nu, tuple(body), frozenset({"connector"}), "strength")
        return Parts(parts=(part,))

    def occlusal_points(self, ctx: BlockContext[CantileverExtensionSettings]) -> tuple[OcclusalPoint, ...]:
        return self._shape(ctx).occlusal_points(ctx)

    def sockets(self, ctx: BlockContext[CantileverExtensionSettings]) -> tuple[Socket, ...]:
        return unit_sockets(ctx, self._shape(ctx), (ctx.settings.joined_socket,))

    def joints(self, ctx: BlockContext[CantileverExtensionSettings]) -> tuple[Joint, ...]:
        return unit_joints(ctx, f"{self.label} {ctx.instance_id!r}", self._shape(ctx), self.sockets(ctx))


BLOCK_TYPES = (CantileverExtension(),)
