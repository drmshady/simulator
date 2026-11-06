"""Abutment with its retention screw (slice 1a; FR-007).

**Settings** (prosthesis group): straight or angled, the angle, the transmucosal collar height, the post height, the
margin diameter, retention by screw or cement, the abutment material and the screw material. The elastic constants
and strength references of both materials come from the library.

**Shape** (local frame: origin at the implant platform centre, +z along the implant axis):

- the **collar**, a frustum from the platform radius (from the implant's `platform` socket) at z = 0 to the margin
  radius at z = `collar_mm`; the margin plane is at right angles to the implant axis;
- the **post**, a frustum tapering at `POST_TAPER_DEG` per side from `SHOULDER_MM` inside the margin, rising
  `height_mm` along the post axis. An angled abutment tilts the post axis by `angle_deg` about the local x-axis,
  toward lingual (−y); the placement's rotation turns the tilt to any side. The post reaches down into the collar
  so the two join without a gap;
- for an internal connection, a **connection post** (cone or hexagon) going into the implant, which it displaces;
- the **retention screw**, its own part tagged `screw`: a head seated `HEAD_SEAT_MM` above the platform (above the
  hex boss for an external hex) and a shank down to the implant's screw bore. It displaces the abutment and the
  implant. Its thread is not modelled (bonded);
- the **screw-access channel** above the head, along the implant axis, left empty: a cutter that removes abutment
  material only, so a cemented crown still covers the channel.

Stress in the screw is reported only once screw preload passes its benchmark; until then it is a locked result.

**Socket** `crown_seat`: origin at the margin centre, z along the post axis. Properties: `margin_radius_mm`,
`margin_tilt_deg` (the margin plane is the seat's xy-plane turned by −margin_tilt_deg about the seat's x-axis),
`core_height_mm`, `core_base_radius_mm`, `core_top_radius_mm`, `retention`, `channel_radius_mm` (the access
channel runs along the margin plane's normal through the seat origin), and `x_toward` passed on from the platform.
"""

from __future__ import annotations

import math
from typing import Annotated, Literal

from dentalsim.blocks.base import (
    X_TOWARD,
    AttachPoint,
    BlockContext,
    BlockSettings,
    BlockType,
    Cutter,
    Design,
    Library,
    Part,
    Parts,
    RefinementHint,
    ResultType,
    Socket,
)
from dentalsim.geometry import occ as occ_tools
from dentalsim.geometry.shapes import Cylinder, Frustum
from dentalsim.geometry.transform import Transform
from dentalsim.model.validity import Problem

SHOULDER_MM = 0.5
POST_TAPER_DEG = 6.0
POST_OVERLAP_MM = 0.2
HEAD_SEAT_MM = 0.5
HEAD_RADIUS = 1.5
"""Screw head radius as a multiple of the shank radius."""
HEAD_HEIGHT_MM = 1.2
CHANNEL_CLEARANCE_MM = 0.1
CHANNEL_LENGTH_MM = 30.0
MIN_POST_RADIUS_MM = 0.5
MIN_WALL_MM = 0.3


class AbutmentSettings(BlockSettings):
    abutment_type: Annotated[Literal["straight", "angled"], Design("prosthesis", "Abutment type")] = "straight"
    angle_deg: Annotated[
        float, Design("prosthesis", "Abutment angle", unit="°", minimum=0.0, maximum=30.0, help="0 for straight.")
    ] = 0.0
    collar_mm: Annotated[
        float,
        Design("prosthesis", "Collar height", unit="mm", minimum=0.5, maximum=5.0, help="Platform to the margin."),
    ] = 2.0
    height_mm: Annotated[
        float, Design("prosthesis", "Post height", unit="mm", minimum=3.0, maximum=9.0, help="Margin to the top.")
    ] = 4.0
    margin_diameter_mm: Annotated[
        float, Design("prosthesis", "Margin diameter", unit="mm", minimum=3.0, maximum=8.0)
    ] = 5.5
    retention: Annotated[Literal["cement", "screw"], Design("prosthesis", "Crown retention")] = "cement"
    material: Annotated[
        Literal["titanium_g5", "titanium_g4", "zirconia", "cocr"], Design("prosthesis", "Abutment material")
    ] = "titanium_g5"
    E: Annotated[
        float, Library("prosthesis", "Abutment elastic modulus", "material.{material}.E", unit="MPa", above=0.0)
    ]
    nu: Annotated[
        float,
        Library("prosthesis", "Abutment Poisson's ratio", "material.{material}.nu", unit="1", above=-1.0, below=0.5),
    ]
    strength: Annotated[
        float,
        Library(
            "prosthesis",
            "Abutment strength reference",
            "material.{material}.{material:strength}",
            unit="MPa",
            above=0.0,
        ),
    ]
    screw_material: Annotated[Literal["titanium_g5", "titanium_g4"], Design("prosthesis", "Screw material")] = (
        "titanium_g5"
    )
    screw_E: Annotated[
        float, Library("prosthesis", "Screw elastic modulus", "material.{screw_material}.E", unit="MPa", above=0.0)
    ]
    screw_nu: Annotated[
        float,
        Library("prosthesis", "Screw Poisson's ratio", "material.{screw_material}.nu", unit="1", above=-1.0, below=0.5),
    ]
    screw_strength: Annotated[
        float,
        Library(
            "prosthesis",
            "Screw strength reference",
            "material.{screw_material}.{screw_material:strength}",
            unit="MPa",
            above=0.0,
        ),
    ]

    @property
    def margin_radius(self) -> float:
        return self.margin_diameter_mm / 2

    @property
    def post_base_radius(self) -> float:
        return self.margin_radius - SHOULDER_MM

    @property
    def post_top_radius(self) -> float:
        return self.post_base_radius - self.height_mm * math.tan(math.radians(POST_TAPER_DEG))

    @property
    def post_reach_below(self) -> float:
        """How far the post reaches below the margin plane, so a tilted post still meets the collar everywhere."""
        if self.angle_deg == 0:
            return 0.0
        return self.post_base_radius * math.tan(math.radians(self.angle_deg)) + POST_OVERLAP_MM


def seat_transform(s: AbutmentSettings) -> Transform:
    """The crown seat (margin centre, post axis) in the abutment's local frame."""
    return Transform.translation_by((0.0, 0.0, s.collar_mm)) @ Transform.rotation_about((1, 0, 0), s.angle_deg)


class Abutment(BlockType[AbutmentSettings]):
    type_id = "abutment"
    version = "1.0.0"
    slice = "1a"
    label = "Abutment"
    description = "A straight or angled abutment with its retention screw, for a cemented or screw-retained crown."
    settings_model = AbutmentSettings
    attach_points = (AttachPoint("crown_seat", "Crown seat", "The abutment margin, where a crown sits."),)
    attaches_to = ("platform",)
    result_types = (
        ResultType("abutment_stress", "Abutment stress", regions=("abutment",)),
        ResultType(
            "screw_stress", "Retention screw stress", regions=("screw",), needs_physics=frozenset({"screw_preload"})
        ),
        ResultType(
            "screw_fatigue_life",
            "Retention screw fatigue life",
            regions=("screw",),
            needs_physics=frozenset({"screw_preload", "fatigue"}),
        ),
    )
    report_regions = ("abutment", "screw")

    def rules(self, settings: AbutmentSettings, instance_id: str) -> list[Problem]:
        s, messages = settings, []
        if s.abutment_type == "straight" and s.angle_deg != 0:
            messages.append("a straight abutment has no angle; set the angle to 0 or choose an angled abutment")
        if s.abutment_type == "angled" and s.angle_deg == 0:
            messages.append("an angled abutment needs an angle above 0°")
        if s.post_top_radius < MIN_POST_RADIUS_MM:
            messages.append(
                f"the post narrows to {max(s.post_top_radius, 0):.2f} mm radius at its top (at least "
                f"{MIN_POST_RADIUS_MM:g} mm); lower the post or widen the margin"
            )
        if s.post_reach_below > s.collar_mm:
            messages.append(
                f"a {s.angle_deg:g}° post needs a collar of at least {s.post_reach_below:.1f} mm at this margin "
                "diameter"
            )
        return [self.design_problem(instance_id, f"Abutment {instance_id!r}: {m}.") for m in messages]

    def check(self, ctx: BlockContext[AbutmentSettings]) -> list[Problem]:
        if ctx.parent is None or ctx.parent.socket.name != "platform":
            return []
        s, platform = ctx.settings, ctx.parent.socket
        channel = HEAD_RADIUS * platform.number("screw_radius_mm") + CHANNEL_CLEARANCE_MM
        problems = []
        if s.angle_deg == 0 and s.post_top_radius - channel < MIN_WALL_MM:
            problems.append(
                ctx.problem(
                    "design_limit",
                    f"Abutment {ctx.instance_id!r}: the screw channel leaves {s.post_top_radius - channel:.2f} mm of "
                    f"wall at the top of the post (at least {MIN_WALL_MM:g} mm); widen the margin or lower the post.",
                    ctx.to_frame.point((0.0, 0.0, s.collar_mm + s.height_mm)),
                )
            )
        seat = HEAD_SEAT_MM + platform.number("boss_height_mm")
        if seat + HEAD_HEIGHT_MM > s.collar_mm + s.height_mm * math.cos(math.radians(s.angle_deg)):
            problems.append(
                ctx.problem(
                    "design_limit",
                    f"Abutment {ctx.instance_id!r} is too short to hold its retention screw head.",
                    ctx.to_frame.origin,
                )
            )
        return problems

    # --- geometry -----------------------------------------------------------------------------------------------

    def build_geometry(self, ctx: BlockContext[AbutmentSettings]) -> Parts:
        s, platform = ctx.settings, ctx.parent_socket("platform")
        connection = platform.text("connection")
        solids = Frustum((0, 0, 0), (0, 0, s.collar_mm), platform.number("platform_radius_mm"), s.margin_radius)
        tags = solids.build_occ()
        reach = s.post_reach_below
        taper = math.tan(math.radians(POST_TAPER_DEG))
        post = Frustum((0, 0, -reach), (0, 0, s.height_mm), s.post_base_radius + reach * taper, s.post_top_radius)
        post_tags = post.build_occ()
        occ_tools.transform_volumes(post_tags, seat_transform(s))
        tags += post_tags
        depth = platform.number("connection_depth_mm")
        if connection == "internal_conical":
            top = platform.number("connection_radius_mm")
            bottom = top - depth * math.tan(math.radians(platform.number("connection_taper_deg")))
            tags += Frustum((0, 0, 0), (0, 0, -depth), top, bottom).build_occ()
        elif connection == "internal_hex":
            tags.append(occ_tools.hexagon_prism(2 * platform.number("connection_radius_mm"), -depth, 0.0))
        body = occ_tools.fuse(tags)

        shank = platform.number("screw_radius_mm")
        seat = HEAD_SEAT_MM + platform.number("boss_height_mm")
        head_top = seat + HEAD_HEIGHT_MM
        screw = occ_tools.fuse(
            Cylinder((0, 0, seat), (0, 0, head_top), HEAD_RADIUS * shank).build_occ()
            + Cylinder((0, 0, -platform.number("screw_depth_mm")), (0, 0, seat), shank).build_occ()
        )
        channel = Cylinder(
            (0, 0, head_top), (0, 0, head_top + CHANNEL_LENGTH_MM), HEAD_RADIUS * shank + CHANNEL_CLEARANCE_MM
        ).build_occ()

        displaces = {"crown", "bone"} | ({"implant"} if connection.startswith("internal") else set())
        return Parts(
            parts=(
                Part("body", "abutment", s.material, s.E, s.nu, tuple(body), frozenset(displaces), "strength"),
                Part(
                    "screw",
                    "screw",
                    s.screw_material,
                    s.screw_E,
                    s.screw_nu,
                    tuple(screw),
                    frozenset({"abutment", "implant"}),
                    "screw_strength",
                ),
            ),
            cutters=(Cutter("screw_access", tuple(channel), frozenset({"abutment"})),),
            hints=(RefinementHint("screw", "screw", feature_mm=shank, extent_mm=0.5),),
        )

    def sockets(self, ctx: BlockContext[AbutmentSettings]) -> tuple[Socket, ...]:
        s = ctx.settings
        channel = 0.0
        if ctx.parent is not None and ctx.parent.socket.name == "platform":
            channel = HEAD_RADIUS * ctx.parent.socket.number("screw_radius_mm") + CHANNEL_CLEARANCE_MM
        return (
            ctx.socket(
                "crown_seat",
                seat_transform(s),
                {
                    "margin_radius_mm": s.margin_radius,
                    "margin_tilt_deg": s.angle_deg,
                    "core_height_mm": s.height_mm,
                    "core_base_radius_mm": s.post_base_radius,
                    "core_top_radius_mm": s.post_top_radius,
                    "retention": s.retention,
                    "channel_radius_mm": channel,
                    X_TOWARD: ctx.x_toward,
                },
            ),
        )


BLOCK_TYPES = (Abutment(),)
