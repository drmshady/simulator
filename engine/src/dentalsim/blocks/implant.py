"""Implant: a root-form implant body with an axisymmetric thread (slice 1a; FR-007).

**Settings** (implant group): diameter, length, smooth collar, thread design (V, buttress, square, or none for a
plain cylinder as a named sensitivity case), thread pitch and depth, connection type, material and healing state.
The material's elastic constants and strength reference come from the library. An immediately loaded implant also
needs the contact physics (micromotion), so its scenarios stay locked until contact passes its benchmark.

The geometry is the same at every detail level (conventions, "Detail levels"): threads are on or off by the
`thread` setting, never by the mesh.

**Shape** (local frame: origin at the platform centre, +z along the implant axis toward occlusal): a solid of
revolution. From the platform down: the smooth collar at the full radius, a 45° run-out to the thread core, the
thread teeth to the start of the apex, and an apex tapering over `apex_length` to 60% of the core radius. For
`none`, the body is a plain cylinder of the full diameter with the same apex. An external hex adds a hexagonal boss
above the platform.

**Connection.** The internal connection (conical or hexagonal socket) and the screw bore belong to the abutment and
its screw: they displace the implant where they enter it. The rules below scale with the diameter; they are this
block's generic implant design, not a product's:

| Item | Rule |
|---|---|
| Internal conical socket | top radius 0.32 D, half-angle 8°, 3.0 mm deep |
| Internal hex socket | across flats 0.6 D, 2.0 mm deep |
| External hex boss | across flats 0.66 D, 0.7 mm high |
| Retention screw | radius 0.2 D; its bore ends 4.0 mm below the connection, and at least 2.0 mm above the apex |

**Socket** `platform`: the platform centre, axes as the implant. Properties: `connection`, `platform_radius_mm`,
`connection_depth_mm`, `connection_radius_mm` (cone top radius, or half the hex across flats),
`connection_taper_deg`, `screw_radius_mm`, `screw_depth_mm`, `boss_height_mm`, `boss_across_flats_mm`, and
`x_toward` passed on from its site.

**Checks**: the implant must stay in the bone (`exceeds_bone`, beyond `EXPOSURE_TOLERANCE_MM` below the crest) and
out of every keep-out volume of other blocks (`in_nerve_canal`, `in_sinus`, `in_root`). Points of the implant's
outer envelope are tested, set `SAMPLE_INSET_MM` inside it, so an implant that only touches a boundary passes.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Literal

import numpy as np

from dentalsim.blocks.base import (
    X_TOWARD,
    AttachPoint,
    BlockContext,
    BlockSettings,
    BlockType,
    Design,
    Library,
    Part,
    Parts,
    RefinementHint,
    ResultType,
    Socket,
    keep_out_intrusions,
)
from dentalsim.conventions.regions import ReportAnchor
from dentalsim.geometry import occ as occ_tools
from dentalsim.geometry.transform import FloatArray
from dentalsim.model.settings import SettingValue
from dentalsim.model.validity import Problem

ThreadDesign = Literal["v_thread", "buttress", "square", "none"]
Connection = Literal["internal_conical", "internal_hex", "external_hex"]

APEX_RADIUS_FRACTION = 0.6
CONICAL = {"radius": 0.32, "half_angle_deg": 8.0, "depth_mm": 3.0}
INTERNAL_HEX = {"across_flats": 0.6, "depth_mm": 2.0}
EXTERNAL_HEX = {"across_flats": 0.66, "height_mm": 0.7}
SCREW_RADIUS = 0.2
SCREW_BELOW_CONNECTION_MM = 4.0
SCREW_ABOVE_APEX_MM = 2.0
MIN_WALL_MM = 0.3
MIN_SCREW_ENGAGEMENT_MM = 1.0
MIN_THREAD_LAND_MM = 0.05

EXPOSURE_TOLERANCE_MM = 0.5
"""Implant surface outside the bone counts as leaving it only deeper than this below the crest."""
SAMPLE_INSET_MM = 0.02
SAMPLE_STEP_MM = 0.2
SAMPLE_ANGLES = 48


@dataclass(frozen=True)
class ThreadProfile:
    """One tooth: flank angles from the radial direction (coronal and apical side) and the crest width as a
    fraction of the pitch."""

    coronal_flank_deg: float
    apical_flank_deg: float
    crest_fraction: float


THREADS: dict[str, ThreadProfile] = {
    "v_thread": ThreadProfile(30.0, 30.0, 0.1),
    "buttress": ThreadProfile(30.0, 5.0, 0.1),
    "square": ThreadProfile(0.0, 0.0, 0.5),
}


class ImplantSettings(BlockSettings):
    diameter_mm: Annotated[float, Design("implant", "Diameter", unit="mm", minimum=3.0, maximum=6.5)] = 4.1
    length_mm: Annotated[float, Design("implant", "Length", unit="mm", minimum=6.0, maximum=16.0)] = 10.0
    collar_mm: Annotated[
        float, Design("implant", "Smooth collar", unit="mm", minimum=0.0, maximum=3.0, help="Unthreaded neck height.")
    ] = 0.5
    thread: Annotated[
        ThreadDesign,
        Design("implant", "Thread design", help="'none' is a plain cylinder, for a threads-on-or-off comparison."),
    ] = "v_thread"
    thread_pitch_mm: Annotated[float, Design("implant", "Thread pitch", unit="mm", minimum=0.4, maximum=1.5)] = 0.8
    thread_depth_mm: Annotated[float, Design("implant", "Thread depth", unit="mm", minimum=0.1, maximum=0.6)] = 0.35
    connection: Annotated[Connection, Design("implant", "Connection")] = "internal_conical"
    material: Annotated[Literal["titanium_g4", "titanium_g5"], Design("implant", "Material")] = "titanium_g4"
    healing_state: Annotated[
        Literal["osseointegrated", "immediately_loaded"],
        Design("implant", "Healing state", help="An immediately loaded implant needs the contact physics."),
    ] = "osseointegrated"
    E: Annotated[float, Library("implant", "Elastic modulus", "material.{material}.E", unit="MPa", above=0.0)]
    nu: Annotated[
        float, Library("implant", "Poisson's ratio", "material.{material}.nu", unit="1", above=-1.0, below=0.5)
    ]
    strength: Annotated[
        float,
        Library("implant", "Strength reference", "material.{material}.{material:strength}", unit="MPa", above=0.0),
    ]


@dataclass(frozen=True)
class ImplantShape:
    """Dimensions derived from the settings, in mm."""

    radius: float
    core_radius: float
    length: float
    collar: float
    apex_length: float
    apex_radius: float
    threaded: bool

    @classmethod
    def of(cls, s: ImplantSettings) -> ImplantShape:
        radius = s.diameter_mm / 2
        threaded = s.thread != "none"
        core = radius - s.thread_depth_mm if threaded else radius
        return cls(
            radius=radius,
            core_radius=core,
            length=s.length_mm,
            collar=s.collar_mm,
            apex_length=min(1.5, 0.15 * s.length_mm),
            apex_radius=APEX_RADIUS_FRACTION * core,
            threaded=threaded,
        )

    @property
    def thread_start(self) -> float:
        """Depth below the platform where the thread core begins (after the 45° run-out)."""
        return self.collar + (self.radius - self.core_radius)

    @property
    def thread_end(self) -> float:
        return self.length - self.apex_length

    def radius_profile(self) -> tuple[tuple[float, float], ...]:
        """(depth, radius) of the thread core, platform to apex, for the region rules (conventions: core radius)."""
        if not self.threaded:
            return ((0.0, self.radius), (self.thread_end, self.radius), (self.length, self.apex_radius))
        top = ((0.0, self.radius), (self.collar, self.radius)) if self.collar > 0 else ((0.0, self.radius),)
        return (
            *top,
            (self.thread_start, self.core_radius),
            (self.thread_end, self.core_radius),
            (self.length, self.apex_radius),
        )

    def envelope_radius(self, depth: FloatArray) -> FloatArray:
        """The outer radius (thread crests) at each depth below the platform."""
        apex_fraction = np.clip((depth - self.thread_end) / self.apex_length, 0.0, 1.0)
        start = self.radius if not self.threaded else self.core_radius
        tapering = start + (self.apex_radius - start) * apex_fraction
        return np.asarray(np.where(depth <= self.thread_end, self.radius, tapering), dtype=np.float64)


def connection_properties(s: ImplantSettings, shape: ImplantShape) -> dict[str, float | str]:
    d = s.diameter_mm
    depth, radius, taper, boss_height, boss_af = 0.0, 0.0, 0.0, 0.0, 0.0
    if s.connection == "internal_conical":
        depth, radius, taper = CONICAL["depth_mm"], CONICAL["radius"] * d, CONICAL["half_angle_deg"]
    elif s.connection == "internal_hex":
        depth, radius = INTERNAL_HEX["depth_mm"], INTERNAL_HEX["across_flats"] * d / 2
    else:
        boss_height, boss_af = EXTERNAL_HEX["height_mm"], EXTERNAL_HEX["across_flats"] * d
    screw_depth = min(depth + SCREW_BELOW_CONNECTION_MM, shape.length - SCREW_ABOVE_APEX_MM)
    return {
        "connection": s.connection,
        "platform_radius_mm": shape.radius,
        "connection_depth_mm": depth,
        "connection_radius_mm": radius,
        "connection_taper_deg": taper,
        "screw_radius_mm": SCREW_RADIUS * d,
        "screw_depth_mm": screw_depth,
        "boss_height_mm": boss_height,
        "boss_across_flats_mm": boss_af,
    }


def revolve_outline(s: ImplantSettings, shape: ImplantShape) -> list[tuple[float, float]]:
    """The closed (r, z) outline revolved into the body; z = 0 at the platform, negative toward the apex."""
    outline = [(0.0, 0.0), (shape.radius, 0.0)]
    if not shape.threaded:
        outline.append((shape.radius, -shape.thread_end))
    else:
        if shape.collar > 0:
            outline.append((shape.radius, -shape.collar))
        outline.append((shape.core_radius, -shape.thread_start))
        profile, pitch, depth = THREADS[s.thread], s.thread_pitch_mm, s.thread_depth_mm
        rise_in = depth * math.tan(math.radians(profile.coronal_flank_deg))
        crest = profile.crest_fraction * pitch
        rise_out = depth * math.tan(math.radians(profile.apical_flank_deg))
        z = shape.thread_start
        while z + pitch <= shape.thread_end + 1e-9:
            outline += [
                (shape.radius, -(z + rise_in)),
                (shape.radius, -(z + rise_in + crest)),
                (shape.core_radius, -(z + rise_in + crest + rise_out)),
            ]
            z += pitch
            if z < shape.thread_end - 1e-9:
                outline.append((shape.core_radius, -z))
        if outline[-1][1] > -shape.thread_end + 1e-9:
            outline.append((shape.core_radius, -shape.thread_end))
    outline += [(shape.apex_radius, -shape.length), (0.0, -shape.length)]
    return _dedupe(outline)


def _dedupe(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    kept = [points[0]]
    for point in points[1:]:
        if math.dist(point, kept[-1]) > 1e-9:
            kept.append(point)
    return kept


class Implant(BlockType[ImplantSettings]):
    type_id = "implant"
    version = "1.0.0"
    slice = "1a"
    label = "Implant"
    description = "A root-form implant with an axisymmetric V, buttress or square thread, or a plain cylinder."
    settings_model = ImplantSettings
    attach_points = (AttachPoint("platform", "Implant platform", "Where the abutment sits; carries the connection."),)
    attaches_to = ("site",)
    result_types = (
        ResultType("implant_stress", "Implant stress", regions=("implant_neck", "implant")),
        ResultType("micromotion", "Micromotion at the bone interface", needs_physics=frozenset({"contact"})),
        ResultType(
            "implant_fatigue_life", "Implant fatigue life", regions=("implant",), needs_physics=frozenset({"fatigue"})
        ),
    )
    report_regions = ("implant_neck", "implant")
    is_axis_reference = True

    def needs_physics(self, settings: Mapping[str, SettingValue]) -> frozenset[str]:
        if self.design_values(settings)["healing_state"] == "immediately_loaded":
            return self.physics | {"contact"}
        return self.physics

    def rules(self, settings: ImplantSettings, instance_id: str) -> list[Problem]:
        s, shape, messages = settings, ImplantShape.of(settings), []
        if shape.threaded:
            profile = THREADS[s.thread]
            tooth = (
                s.thread_depth_mm
                * (math.tan(math.radians(profile.coronal_flank_deg)) + math.tan(math.radians(profile.apical_flank_deg)))
                + profile.crest_fraction * s.thread_pitch_mm
            )
            if tooth > s.thread_pitch_mm - MIN_THREAD_LAND_MM:
                messages.append(
                    f"a {s.thread_depth_mm:g} mm deep {s.thread.replace('_', ' ')} tooth is {tooth:.2f} mm wide, which "
                    f"does not fit a {s.thread_pitch_mm:g} mm pitch; use a coarser pitch or a shallower thread"
                )
            if shape.thread_end - shape.thread_start < s.thread_pitch_mm:
                messages.append("the collar and apex leave no room for a single thread turn")
        properties = connection_properties(s, shape)
        inner = max(float(properties["connection_radius_mm"]), float(properties["screw_radius_mm"]))
        if shape.core_radius - inner < MIN_WALL_MM:
            messages.append(
                f"the connection leaves {shape.core_radius - inner:.2f} mm of implant wall (at least "
                f"{MIN_WALL_MM:g} mm is needed); use a larger diameter or a shallower thread"
            )
        engagement = float(properties["screw_depth_mm"]) - float(properties["connection_depth_mm"])
        if engagement < MIN_SCREW_ENGAGEMENT_MM:
            messages.append(
                f"a {s.length_mm:g} mm implant is too short for its {s.connection.replace('_', ' ')} "
                "connection and retention screw"
            )
        return [self.design_problem(instance_id, f"Implant {instance_id!r}: {m}.") for m in messages]

    # --- geometry -----------------------------------------------------------------------------------------------

    def build_geometry(self, ctx: BlockContext[ImplantSettings]) -> Parts:
        s, shape = ctx.settings, ImplantShape.of(ctx.settings)
        body = occ_tools.revolve_rz(revolve_outline(s, shape))
        displaces = {"bone"}
        if s.connection == "external_hex":
            boss = occ_tools.hexagon_prism(EXTERNAL_HEX["across_flats"] * s.diameter_mm, 0.0, EXTERNAL_HEX["height_mm"])
            body = occ_tools.fuse([*body, boss])
            displaces.add("abutment")
        feature = s.thread_pitch_mm if shape.threaded else s.diameter_mm / 4
        return Parts(
            parts=(Part("body", "implant", s.material, s.E, s.nu, tuple(body), frozenset(displaces), "strength"),),
            hints=(RefinementHint("threads", "body", feature_mm=feature, extent_mm=2.0),),
        )

    def sockets(self, ctx: BlockContext[ImplantSettings]) -> tuple[Socket, ...]:
        shape = ImplantShape.of(ctx.settings)
        properties = {**connection_properties(ctx.settings, shape), X_TOWARD: ctx.x_toward}
        return (ctx.socket("platform", properties=properties),)

    # --- checks and reporting -----------------------------------------------------------------------------------

    def surface_samples(self, ctx: BlockContext[ImplantSettings]) -> tuple[FloatArray, FloatArray]:
        """Points just inside the implant's outer envelope, in the conventions frame, with their depths below the
        platform."""
        shape = ImplantShape.of(ctx.settings)
        depths = np.linspace(0.0, shape.length, max(2, math.ceil(shape.length / SAMPLE_STEP_MM) + 1))
        angles = np.linspace(0.0, 2 * math.pi, SAMPLE_ANGLES, endpoint=False)
        radii = np.maximum(shape.envelope_radius(depths) - SAMPLE_INSET_MM, 0.0)
        ring_depth = np.minimum(np.repeat(depths, SAMPLE_ANGLES), shape.length - SAMPLE_INSET_MM)
        ring_radius = np.repeat(radii, SAMPLE_ANGLES)
        ring_angle = np.tile(angles, len(depths))
        disc_radius = np.repeat(np.linspace(0.0, shape.apex_radius - SAMPLE_INSET_MM, 4), SAMPLE_ANGLES)
        disc_angle = np.tile(angles, 4)
        disc_depth = np.full(disc_radius.shape, shape.length - SAMPLE_INSET_MM)
        r = np.concatenate([ring_radius, disc_radius])
        a = np.concatenate([ring_angle, disc_angle])
        d = np.concatenate([ring_depth, disc_depth])
        local = np.column_stack([r * np.cos(a), r * np.sin(a), -d])
        return ctx.to_frame.apply(local), d

    def crest_depth(self, ctx: BlockContext[ImplantSettings]) -> float | None:
        """Depth below the platform, along the axis, where the axis enters the bone; None when it never does."""
        if not ctx.environment.bone:
            return None
        length = ImplantShape.of(ctx.settings).length
        depths = np.arange(-10.0, length + 5.0, 0.05)
        inside = ctx.environment.in_bone(ctx.to_frame.apply(np.column_stack([0 * depths, 0 * depths, -depths])))
        if not inside.any():
            return None
        first = int(np.argmax(inside))
        if first == 0:
            return float(depths[0])
        low, high = float(depths[first - 1]), float(depths[first])
        for _ in range(40):
            middle = (low + high) / 2
            if ctx.environment.in_bone(ctx.to_frame.apply((0.0, 0.0, -middle)))[0]:
                high = middle
            else:
                low = middle
        return high

    def check(self, ctx: BlockContext[ImplantSettings]) -> list[Problem]:
        points, depths = self.surface_samples(ctx)
        problems = []
        for keep_out, inside in keep_out_intrusions(ctx.environment.keep_outs, points):
            problems.append(
                ctx.problem(
                    keep_out.code,
                    f"Implant {ctx.instance_id!r} enters {keep_out.label} {_between(depths[inside])}.",
                    points[inside].mean(axis=0),
                )
            )
        if ctx.environment.bone:
            crest = self.crest_depth(ctx)
            if crest is None:
                problems.append(
                    ctx.problem("exceeds_bone", f"Implant {ctx.instance_id!r} is not in the bone.", ctx.to_frame.origin)
                )
            else:
                outside = (
                    (depths > crest + EXPOSURE_TOLERANCE_MM)
                    & ~ctx.environment.in_bone(points)
                    & ~ctx.environment.in_keep_out(points)
                )
                if outside.any():
                    problems.append(
                        ctx.problem(
                            "exceeds_bone",
                            f"Implant {ctx.instance_id!r} is not covered by bone {_between(depths[outside])}: it is "
                            "longer, wider or more tilted than the available bone allows.",
                            points[outside].mean(axis=0),
                        )
                    )
        return problems

    def report_anchors(self, ctx: BlockContext[ImplantSettings]) -> tuple[ReportAnchor, ...]:
        crest = self.crest_depth(ctx)
        return (
            ReportAnchor(
                instance_id=ctx.instance_id,
                component="implant",
                platform_mm=ctx.to_frame.origin,
                axis=ctx.to_frame.direction((0.0, 0.0, 1.0)),
                radius_profile=ImplantShape.of(ctx.settings).radius_profile(),
                crest_depth_mm=0.0 if crest is None else crest,
            ),
        )


def _between(depths: FloatArray) -> str:
    low, high = float(depths.min()), float(depths.max())
    if high - low < 0.05:
        return f"at {low:.1f} mm below its platform"
    return f"from {low:.1f} to {high:.1f} mm below its platform"


BLOCK_TYPES = (Implant(),)
