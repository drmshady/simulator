"""Jaw segment: the bone an assembly sits in, and the root of every assembly (slice 1a, sites of slice 1b; FR-004,
FR-007).

**Settings** (all in the patient-bone group): the jaw; the region as FDI tooth codes, any contiguous run up to a full
arch; the reference site (the frame's origin); the natural teeth present next to the region; the density class
D1–D4; the ridge height and widths; the crestal bone loss; the nerve canal (mandible) or the sinus floor (maxilla);
the arch width. The cortical thickness (by density class), the cortical and cancellous elastic constants come from
the library.

**Arch.** The dental arch is a parabola in the occlusal plane. Teeth sit along it by arc length, each taking its
mean mesiodistal crown width from `TOOTH_WIDTHS_MM` (design geometry of this block, owner to review with the
template); `arch_width_mm`, the distance between the two first-molar centres, sets how strongly it curves. The
site is always built on the positive side of the arch, so a site in a mirrored quadrant (maxillary left,
mandibular right) is built as the mirror image of its counterpart, as the conventions frame requires, without any
reflection reaching the mesh.

**Bone.** The cross-section is a trapezoid with rounded corners: `ridge_width_mm` at the crest, `basal_width_mm`
at the base, `ridge_height_mm` deep, standing upright and swept along the arch from `segment_extension_mm` before
the region to as far beyond it. Its outer layer, `cortical_thickness` thick, is cortical bone; the core is
cancellous bone. The two cut faces, at right angles to the arch, are the supports. In the maxilla the base of the
section is the sinus floor when the sinus is present (the bone above it is the residual bone height).

**Frame.** The local frame is the conventions frame: origin at the crest centre of the reference site, +x mesial
along the arch, +y buccal, +z occlusal. The crestal bone loss lowers the bone, so the crest is at z = 0 and the site
sockets, where implant platforms and pontics go, are `crestal_bone_loss_mm` above it.

**Keep-out volumes**: the nerve canal (a tube along the arch), the maxillary sinus (a slab above the residual
bone), and an envelope around the root of each neighbouring tooth (a cone from the crest to the apex).

**Sockets** `site:<FDI>`, one per tooth of the region (slice 1b), at the crest centre of the tooth's place on the
arch (spaced by the tooth widths): x along the arch, y buccal (outward), z occlusal. On the reference site's side of
the midline x points mesial; past the midline it keeps running the same way along the arch, so it points distal
there (the frame stays right-handed with y buccal, so no reflection reaches the mesh). Placement offsets on such a
site run along its axes. A bare `site` is the reference site, as in slice 1a
scenarios. Properties: `fdi`, `crestal_bone_loss_mm`, `tooth_width_mm` (the tooth's mean mesiodistal width) and
`x_toward` (`mesial` or `distal`, which the blocks built on the site pass on, so they name their sides anatomically).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Literal

import numpy as np
from scipy.optimize import brentq  # type: ignore[import-untyped]

from dentalsim.blocks.base import (
    X_TOWARD,
    AttachPoint,
    BlockContext,
    BlockSettings,
    BlockType,
    Design,
    KeepOut,
    Library,
    Part,
    Parts,
    ResultType,
    Socket,
    SupportPlane,
)
from dentalsim.geometry import occ as occ_tools
from dentalsim.geometry.shapes import Circle2D, Frustum, PathSweep, PlanarPath, RoundedPolygon, Volume
from dentalsim.geometry.transform import FloatArray, Transform
from dentalsim.model.validity import Problem

JawName = Literal["mandible", "maxilla"]

TOOTH_WIDTHS_MM: dict[JawName, tuple[float, ...]] = {
    # Mean mesiodistal crown widths, central incisor (1) to third molar (8): Wheeler's dental anatomy tables.
    "maxilla": (8.5, 6.5, 7.5, 7.0, 6.5, 10.0, 9.0, 8.5),
    "mandible": (5.0, 5.5, 7.0, 7.0, 7.0, 11.0, 10.5, 10.0),
}
FIRST_MOLAR = 6
QUADRANT_JAW: dict[int, JawName] = {1: "maxilla", 2: "maxilla", 3: "mandible", 4: "mandible"}
FDI = re.compile(r"^([1-4])([1-8])$")

CREST_ROUNDING = 0.25
"""Radius of the crest corners as a fraction of the ridge width (and at most a quarter of the height)."""
BASE_ROUNDING = 0.3
"""Radius of the basal corners as a fraction of the basal width (and at most a quarter of the height)."""
MAX_SIDE_SLANT_DEG = 30.0
MIN_CORE_MM = 1.0
"""The cancellous core must be at least this wide and high inside the cortical shell."""
SINUS_DEPTH_MM = 10.0
SINUS_MARGIN_MM = 3.0
ROOT_APEX_FRACTION = 1 / 3
PATH_STEP_MM = 0.2
PATH_MARGIN_MM = 5.0


class JawSegmentSettings(BlockSettings):
    jaw: Annotated[JawName, Design("patient_bone", "Jaw")] = "mandible"
    region: Annotated[
        tuple[str, ...],
        Design(
            "patient_bone",
            "Region",
            min_items=1,
            max_items=16,
            help="The teeth the segment spans, as FDI codes, contiguous along the arch.",
        ),
    ] = ("36",)
    site: Annotated[
        str,
        Design(
            "patient_bone",
            "Reference site",
            help="The FDI code of the tooth whose crest centre is the frame's origin; it is in the region. Implants "
            "and pontics sit at the sites of the region's teeth.",
        ),
    ] = "36"
    neighbours: Annotated[
        tuple[str, ...],
        Design(
            "patient_bone",
            "Neighbouring teeth present",
            max_items=2,
            help="Natural teeth just outside the region, as FDI codes; their roots are kept out of.",
        ),
    ] = ()
    density_class: Annotated[Literal["D1", "D2", "D3", "D4"], Design("patient_bone", "Bone density class")] = "D2"
    ridge_height_mm: Annotated[
        float,
        Design(
            "patient_bone",
            "Ridge height",
            unit="mm",
            minimum=6.0,
            maximum=40.0,
            help="Crest to the base of the segment: the lower border (mandible) or the sinus floor (maxilla).",
        ),
    ] = 24.0
    ridge_width_mm: Annotated[
        float, Design("patient_bone", "Ridge width at the crest", unit="mm", minimum=4.0, maximum=16.0)
    ] = 9.0
    basal_width_mm: Annotated[
        float, Design("patient_bone", "Width at the base", unit="mm", minimum=4.0, maximum=20.0)
    ] = 11.0
    crestal_bone_loss_mm: Annotated[
        float,
        Design(
            "patient_bone",
            "Crestal bone loss",
            unit="mm",
            minimum=0.0,
            maximum=5.0,
            help="How far the bone crest lies below the implant platform level, over the whole segment.",
        ),
    ] = 0.0
    nerve_canal_depth_mm: Annotated[
        float,
        Design(
            "patient_bone",
            "Nerve canal depth",
            unit="mm",
            minimum=2.0,
            maximum=38.0,
            help="Mandible only: from the crest to the top of the canal.",
        ),
    ] = 15.0
    nerve_canal_diameter_mm: Annotated[
        float, Design("patient_bone", "Nerve canal diameter", unit="mm", minimum=1.5, maximum=5.0)
    ] = 3.0
    nerve_canal_offset_mm: Annotated[
        float,
        Design(
            "patient_bone",
            "Nerve canal offset",
            unit="mm",
            minimum=-6.0,
            maximum=6.0,
            help="Mandible only: from the middle of the ridge to the canal centre, positive toward buccal.",
        ),
    ] = 0.0
    sinus: Annotated[
        Literal["absent", "present"],
        Design("patient_bone", "Maxillary sinus", help="Maxilla only: the sinus floor lies at the base of the ridge."),
    ] = "absent"
    arch_width_mm: Annotated[
        float,
        Design(
            "patient_bone",
            "Arch width",
            unit="mm",
            minimum=30.0,
            maximum=70.0,
            help="Between the centres of the two first molars.",
        ),
    ] = 45.0
    segment_extension_mm: Annotated[
        float,
        Design(
            "patient_bone",
            "Segment extension",
            unit="mm",
            minimum=2.0,
            maximum=30.0,
            help="How far the bone reaches beyond the region at each end, to the supported cut faces.",
        ),
    ] = 9.0
    neighbour_root_length_mm: Annotated[
        float, Design("patient_bone", "Neighbouring root length", unit="mm", minimum=6.0, maximum=25.0)
    ] = 13.0
    neighbour_root_radius_mm: Annotated[
        float,
        Design(
            "patient_bone",
            "Neighbouring root radius",
            unit="mm",
            minimum=1.5,
            maximum=6.0,
            help="Radius of the space kept free around a neighbouring root, at the crest.",
        ),
    ] = 3.5
    cortical_thickness: Annotated[
        float,
        Library("patient_bone", "Cortical thickness", "bone.cortical.thickness.{density_class}", unit="mm", above=0.0),
    ]
    cortical_E: Annotated[
        float, Library("patient_bone", "Cortical bone elastic modulus", "bone.cortical.E", unit="MPa", above=0.0)
    ]
    cortical_nu: Annotated[
        float,
        Library("patient_bone", "Cortical bone Poisson's ratio", "bone.cortical.nu", unit="1", above=-1.0, below=0.5),
    ]
    cancellous_E: Annotated[
        float,
        Library(
            "patient_bone",
            "Cancellous bone elastic modulus",
            "bone.cancellous.E.{density_class}",
            unit="MPa",
            above=0.0,
        ),
    ]
    cancellous_nu: Annotated[
        float,
        Library(
            "patient_bone", "Cancellous bone Poisson's ratio", "bone.cancellous.nu", unit="1", above=-1.0, below=0.5
        ),
    ]


# --- teeth and the arch ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Tooth:
    fdi: str
    quadrant: int
    position: int

    @property
    def jaw(self) -> JawName:
        return QUADRANT_JAW[self.quadrant]


def parse_fdi(code: str) -> Tooth | None:
    match = FDI.match(code)
    return Tooth(code, int(match[1]), int(match[2])) if match else None


def arch_key(tooth: Tooth, site_quadrant: int) -> int:
    """The tooth's place along the arch: +position on the site's side, -position on the other side, so that
    sorting the keys gives the arch order (… -2, -1, 1, 2 …)."""
    return tooth.position if tooth.quadrant == site_quadrant else -tooth.position


def _next_key(key: int, step: int) -> int:
    following = key + step
    return following + step if following == 0 else following


def _arc_length(a: float, x: FloatArray | float) -> FloatArray:
    """Arc length of y = a·x² from the vertex to x ≥ 0."""
    x = np.asarray(x, dtype=np.float64)
    return np.asarray(0.5 * x * np.sqrt(1 + 4 * a * a * x * x) + np.arcsinh(2 * a * x) / (4 * a), dtype=np.float64)


@dataclass(frozen=True, eq=False)
class Arch:
    """The dental arch, y = a·x² in arch coordinates (x toward the site's side, y posterior), with its teeth.

    `sigma` is the signed arc length from the midline: positive on the site's side.
    """

    widths: tuple[float, ...]
    a: float
    _x: FloatArray
    _s: FloatArray

    @classmethod
    def build(cls, jaw: JawName, arch_width_mm: float, reach_mm: float) -> Arch:
        widths = TOOTH_WIDTHS_MM[jaw]
        molar = sum(widths[: FIRST_MOLAR - 1]) + widths[FIRST_MOLAR - 1] / 2
        half_width = arch_width_mm / 2
        if half_width >= molar:
            raise ValueError(f"an arch {arch_width_mm:g} mm wide is wider than its teeth allow ({2 * molar:g} mm)")
        a = float(brentq(lambda k: float(_arc_length(k, half_width)) - molar, 1e-9, 10.0, xtol=1e-14))
        x = np.linspace(0.0, reach_mm, max(2, math.ceil(reach_mm / 0.01) + 1))  # S(x) >= x, so x = reach suffices
        return cls(widths, a, x, _arc_length(a, x))

    def centre(self, key: int) -> float:
        position = abs(key)
        return math.copysign(sum(self.widths[: position - 1]) + self.widths[position - 1] / 2, key)

    def extent(self, key: int) -> tuple[float, float]:
        centre, half = self.centre(key), self.widths[abs(key) - 1] / 2
        return centre - half, centre + half

    def xy(self, sigma: FloatArray) -> FloatArray:
        sigma = np.asarray(sigma, dtype=np.float64)
        if np.abs(sigma).max(initial=0.0) > self._s[-1]:
            raise ValueError("the arch is not long enough for this segment")
        x = np.sign(sigma) * np.interp(np.abs(sigma), self._s, self._x)
        return np.column_stack([x, self.a * x * x])


@dataclass(frozen=True, eq=False)
class JawLayout:
    """Everything the geometry, keep-outs and supports are built from, in the local (= conventions) frame."""

    arch: Arch
    path: PlanarPath
    site_key: int
    keys: dict[str, int]
    segment: tuple[float, float]
    outer: RoundedPolygon
    inner: RoundedPolygon


def outer_profile(settings: JawSegmentSettings) -> RoundedPolygon:
    top, base, height = settings.ridge_width_mm / 2, settings.basal_width_mm / 2, settings.ridge_height_mm
    crest_radius = min(CREST_ROUNDING * settings.ridge_width_mm, 0.25 * height)
    base_radius = min(BASE_ROUNDING * settings.basal_width_mm, 0.25 * height)
    return RoundedPolygon(
        ((-base, -height), (base, -height), (top, 0.0), (-top, 0.0)),
        (base_radius, base_radius, crest_radius, crest_radius),
    )


def nerve_canal_profile(settings: JawSegmentSettings) -> Circle2D:
    radius = settings.nerve_canal_diameter_mm / 2
    return Circle2D((settings.nerve_canal_offset_mm, -(settings.nerve_canal_depth_mm + radius)), radius)


def sinus_profile(settings: JawSegmentSettings) -> RoundedPolygon:
    half = max(settings.ridge_width_mm, settings.basal_width_mm) / 2 + SINUS_MARGIN_MM
    floor = -settings.ridge_height_mm
    return RoundedPolygon(
        ((-half, floor - SINUS_DEPTH_MM), (half, floor - SINUS_DEPTH_MM), (half, floor), (-half, floor)), (0.0,) * 4
    )


@lru_cache(maxsize=32)
def layout(settings: JawSegmentSettings) -> JawLayout:
    site = parse_fdi(settings.site)
    if site is None:
        raise ValueError(f"{settings.site!r} is not an FDI tooth code")
    keys = {}
    for code in (*settings.region, *settings.neighbours):
        tooth = parse_fdi(code)
        if tooth is None:
            raise ValueError(f"{code!r} is not an FDI tooth code")
        keys[code] = arch_key(tooth, site.quadrant)
    outer = outer_profile(settings)
    inner = outer.offset(settings.cortical_thickness)
    reach = max(abs(v) for corner in sinus_profile(settings).uv_bounds() for v in corner[:1]) + PATH_MARGIN_MM
    total = sum(TOOTH_WIDTHS_MM[settings.jaw]) + settings.segment_extension_mm + 2 * reach
    arch = Arch.build(settings.jaw, settings.arch_width_mm, total)
    extents = [arch.extent(keys[code]) for code in settings.region]
    segment = (
        min(e[0] for e in extents) - settings.segment_extension_mm,
        max(e[1] for e in extents) + settings.segment_extension_mm,
    )
    centres = [arch.centre(keys[code]) for code in settings.neighbours]
    low, high = min([segment[0], *centres]) - reach, max([segment[1], *centres]) + reach
    sigma = np.linspace(low, high, max(2, math.ceil((high - low) / PATH_STEP_MM) + 1))
    site_sigma = arch.centre(keys[settings.site])
    origin = arch.xy(np.array([site_sigma]))[0]
    ahead, behind = arch.xy(np.array([site_sigma + 1e-4]))[0], arch.xy(np.array([site_sigma - 1e-4]))[0]
    tangent = (ahead - behind) / np.linalg.norm(ahead - behind)
    mesial = -tangent
    buccal = np.array([tangent[1], -tangent[0]])  # right of travel along increasing sigma: outward from the arch
    offset = arch.xy(sigma) - origin
    local = np.column_stack([offset @ mesial, offset @ buccal])
    return JawLayout(arch, PlanarPath(local, s0=float(low)), keys[settings.site], keys, segment, outer, inner)


# --- the block type ----------------------------------------------------------------------------------------------


def _fdi_problems(settings: JawSegmentSettings) -> list[str]:
    jaw, messages = settings.jaw, []
    region = [parse_fdi(code) for code in settings.region]
    for code, tooth in zip(settings.region, region, strict=True):
        if tooth is None:
            messages.append(f"region: {code!r} is not an FDI tooth code (11–48)")
        elif tooth.jaw != jaw:
            messages.append(f"region: tooth {code} is in the {tooth.jaw}, not the {jaw}")
    if len(set(settings.region)) != len(settings.region):
        messages.append("region: a tooth is listed twice")
    site = parse_fdi(settings.site)
    if site is None or site.jaw != jaw:
        messages.append(f"site: {settings.site!r} is not a tooth of the {jaw}")
    elif settings.site not in settings.region:
        messages.append(f"site: tooth {settings.site} is not in the region")
    if messages or site is None:
        return messages
    keys = sorted(arch_key(t, site.quadrant) for t in region if t is not None)
    if any(_next_key(a, 1) != b for a, b in zip(keys, keys[1:], strict=False)):
        messages.append("region: the teeth must follow each other along the arch, with no gap")
        return messages
    for code in settings.neighbours:
        tooth = parse_fdi(code)
        if tooth is None or tooth.jaw != jaw:
            messages.append(f"neighbours: {code!r} is not a tooth of the {jaw}")
        elif code in settings.region:
            messages.append(f"neighbours: tooth {code} is in the region")
        elif arch_key(tooth, site.quadrant) not in (_next_key(keys[0], -1), _next_key(keys[-1], 1)):
            messages.append(f"neighbours: tooth {code} is not next to the region")
    if len(set(settings.neighbours)) != len(settings.neighbours):
        messages.append("neighbours: a tooth is listed twice")
    return messages


class JawSegment(BlockType[JawSegmentSettings]):
    type_id = "jaw_segment"
    version = "1.0.0"
    slice = "1a"
    label = "Jaw segment"
    description = "A segment of the mandible or maxilla: cortical shell, cancellous core, nerve canal or sinus floor."
    settings_model = JawSegmentSettings
    attach_points = (
        AttachPoint(
            "site",
            "Site",
            "The crest centre of a tooth of the region, where an implant platform or a pontic sits.",
            keys_from="region",
            default_key_from="site",
        ),
    )
    attaches_to = ()
    result_types = (ResultType("bone_strain", "Bone strain", regions=("crestal_ring",)),)
    report_regions = ("crestal_ring",)

    def rules(self, settings: JawSegmentSettings, instance_id: str) -> list[Problem]:
        messages = _fdi_problems(settings)
        top, base = settings.ridge_width_mm / 2, settings.basal_width_mm / 2
        slant = math.degrees(math.atan2(abs(base - top), settings.ridge_height_mm))
        if slant > MAX_SIDE_SLANT_DEG:
            messages.append(
                f"the sides of the ridge slope by {slant:.0f}°; the crest and base widths may differ by at most "
                f"{2 * settings.ridge_height_mm * math.tan(math.radians(MAX_SIDE_SLANT_DEG)):.1f} mm at this height"
            )
        else:
            messages += self._core_problems(settings)
        if settings.jaw == "mandible" and settings.sinus == "present":
            messages.append("the maxillary sinus applies to the maxilla only; set it to absent")
        return [self.design_problem(instance_id, f"Jaw segment {instance_id!r}: {m}.") for m in messages]

    def _core_problems(self, settings: JawSegmentSettings) -> list[str]:
        outer = outer_profile(settings)
        try:
            (u0, v0), (u1, v1) = outer.offset(settings.cortical_thickness).uv_bounds()
            core = min(u1 - u0, v1 - v0)
        except ValueError:
            core = 0.0
        if core < MIN_CORE_MM:
            return [
                f"the ridge is too thin for a {settings.cortical_thickness:g} mm cortical shell around a cancellous "
                f"core of at least {MIN_CORE_MM:g} mm"
            ]
        if settings.jaw == "mandible":
            canal = nerve_canal_profile(settings)
            angles = np.linspace(0, 2 * math.pi, 73)
            u = canal.centre[0] + canal.radius * np.cos(angles)
            v = canal.centre[1] + canal.radius * np.sin(angles)
            if not outer.contains_uv(u, v).all():
                return ["the nerve canal lies partly outside the bone; check its depth, diameter and offset"]
        return []

    # --- geometry -----------------------------------------------------------------------------------------------

    def build_geometry(self, ctx: BlockContext[JawSegmentSettings]) -> Parts:
        s, plan = ctx.settings, layout(ctx.settings)
        start, end = plan.segment
        outer = PathSweep(plan.path, plan.outer, start, end).build_occ()
        inner = PathSweep(plan.path, plan.inner, start, end).build_occ()
        cortical = occ_tools.cut(outer, inner, keep_tools=True)
        supports = []
        for name, sigma, sign in (("mesial_cut", start, -1.0), ("distal_cut", end, 1.0)):
            point, tangent = plan.path.at(sigma)
            label = name if name != "mesial_cut" or start >= 0 else "contralateral_cut"
            supports.append(SupportPlane(label, _p(point), _p(sign * tangent)))
        return Parts(
            parts=(
                Part("cortical", "bone", "cortical_bone", s.cortical_E, s.cortical_nu, tuple(cortical)),
                Part("cancellous", "bone", "cancellous_bone", s.cancellous_E, s.cancellous_nu, tuple(inner)),
            ),
            supports=tuple(supports),
        )

    def sockets(self, ctx: BlockContext[JawSegmentSettings]) -> tuple[Socket, ...]:
        s, plan = ctx.settings, layout(ctx.settings)
        point = self.attach_points[0]
        found = []
        for code in s.region:
            key = plan.keys[code]
            if code == s.site:
                local = Transform.translation_by((0.0, 0.0, s.crestal_bone_loss_mm))  # the origin, exactly
            else:
                at, tangent = plan.path.at(plan.arch.centre(key))
                along = -tangent
                local = Transform.from_axes(
                    (float(at[0]), float(at[1]), s.crestal_bone_loss_mm), _p(along), _p(np.cross((0, 0, 1), along))
                )
            properties: dict[str, float | str] = {
                "fdi": code,
                "crestal_bone_loss_mm": s.crestal_bone_loss_mm,
                "tooth_width_mm": plan.arch.widths[abs(key) - 1],
                X_TOWARD: "mesial" if key > 0 else "distal",
            }
            found.append(ctx.socket(point.socket_name(code), local, properties))
        return tuple(found)

    def bone_volumes(self, ctx: BlockContext[JawSegmentSettings]) -> tuple[Volume, ...]:
        plan = layout(ctx.settings)
        return (ctx.place(PathSweep(plan.path, plan.outer, *plan.segment)),)

    def keep_out(self, ctx: BlockContext[JawSegmentSettings]) -> tuple[KeepOut, ...]:
        s, plan = ctx.settings, layout(ctx.settings)
        found = []
        if s.jaw == "mandible":
            canal = PathSweep(plan.path, nerve_canal_profile(s), *plan.segment)
            found.append(KeepOut("nerve_canal", "the nerve canal", "in_nerve_canal", ctx.place(canal), ctx.instance_id))
        elif s.sinus == "present":
            sinus = PathSweep(plan.path, sinus_profile(s), *plan.segment)
            found.append(KeepOut("sinus", "the maxillary sinus", "in_sinus", ctx.place(sinus), ctx.instance_id))
        for code in s.neighbours:
            point, _ = plan.path.at(plan.arch.centre(plan.keys[code]))
            top = (float(point[0]), float(point[1]), 0.0)
            apex = (top[0], top[1], -s.neighbour_root_length_mm)
            root = Frustum(top, apex, s.neighbour_root_radius_mm, s.neighbour_root_radius_mm * ROOT_APEX_FRACTION)
            found.append(
                KeepOut(f"root_{code}", f"the root of tooth {code}", "in_root", ctx.place(root), ctx.instance_id)
            )
        return tuple(found)


def _p(values: FloatArray) -> tuple[float, float, float]:
    return (float(values[0]), float(values[1]), float(values[2]))


BLOCK_TYPES = (JawSegment(),)
