"""The machine-readable conventions block of docs/conventions.md (FR-050, research R6).

Every model refuses unknown fields, so a misspelt key fails the check instead of being ignored.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
HEX_COLOUR = re.compile(r"^#[0-9A-Fa-f]{6}$")
KEY_PART = re.compile(r"^[A-Za-z0-9_]+$")
PATTERN_PART = re.compile(r"^([A-Za-z0-9_]+|\*)$")

LibraryCategory = Literal["material", "bone", "load", "strength", "frost_window"]
LIBRARY_CATEGORIES: tuple[LibraryCategory, ...] = ("material", "bone", "load", "strength", "frost_window")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Units(_Model):
    length: Literal["mm"]
    force: Literal["N"]
    stress: Literal["MPa"]
    mass: Literal["tonne"]
    time: Literal["s"]
    strain: Literal["microstrain"]


class Axis(_Model):
    direction: str
    vector: tuple[float, float, float]


class Axes(_Model):
    x: Axis
    y: Axis
    z: Axis


Quadrant = Literal["maxillary_right", "maxillary_left", "mandibular_left", "mandibular_right"]


class Frame(_Model):
    """An abstract right-handed frame. Anatomically it is a site in `anatomical_quadrants` (mesial × buccal =
    occlusal there); sites in the other quadrants are mirrored into it (`other_quadrants`)."""

    origin: str
    handedness: Literal["right"]
    anatomical_quadrants: tuple[Quadrant, ...]
    other_quadrants: Literal["mirrored"]
    axes: Axes

    @field_validator("anatomical_quadrants")
    @classmethod
    def _right_handed_quadrants(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if set(value) != {"maxillary_right", "mandibular_left"}:
            raise ValueError(
                "mesial × buccal = occlusal holds for maxillary-right and mandibular-left sites only; "
                "anatomical_quadrants must be exactly those two"
            )
        return value

    @model_validator(mode="after")
    def _orthonormal_and_right_handed(self) -> Frame:
        x, y, z = self.axes.x.vector, self.axes.y.vector, self.axes.z.vector

        def dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
            return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

        for a, b in ((x, x), (y, y), (z, z)):
            if abs(dot(a, b) - 1.0) > 1e-9:
                raise ValueError("the frame axes must be unit vectors")
        for a, b in ((x, y), (y, z), (z, x)):
            if abs(dot(a, b)) > 1e-9:
                raise ValueError("the frame axes must be at right angles to each other")
        cross = (x[1] * y[2] - x[2] * y[1], x[2] * y[0] - x[0] * y[2], x[0] * y[1] - x[1] * y[0])
        if dot(cross, z) < 0:
            raise ValueError("the frame must be right-handed: x × y = z")
        return self


class LoadAngle(_Model):
    """How `angle_deg` and `azimuth_deg` of a load become a direction (see `conventions.frame.force_direction`)."""

    zero: Literal["axial"]
    azimuth_reference: Literal["mesial"]
    azimuth_toward: Literal["buccal"]
    positive_tilt: Literal["away_from_azimuth"]
    default_azimuth_deg: float = Field(ge=0, lt=360)
    angle_range_deg: tuple[float, float]

    @field_validator("angle_range_deg")
    @classmethod
    def _range(cls, value: tuple[float, float]) -> tuple[float, float]:
        low, high = value
        if not 0 <= low < high <= 90:
            raise ValueError("angle_range_deg must lie within [0, 90]; a negative angle duplicates azimuth + 180°")
        return value


class LoadPatch(_Model):
    """A uniform traction (in the load direction) over the surface faces whose centroids lie within `radius_mm`."""

    radius_mm: float = Field(gt=0)
    selection: Literal["face_centroid_within_radius"]
    application: Literal["uniform_traction"]
    crown_stats_exclude_radius_mm: float = Field(ge=0)


class Analysis(_Model):
    """The assumptions under which unit-load superposition is valid."""

    type: Literal["linear_static"]
    interfaces: Literal["tied"]
    contact: Literal["none"]
    screw_preload: Literal["none"]
    superposition: Literal["unit_load_cases"]


class DetailLevels(_Model):
    vary: Literal["mesh_density_only"]
    geometry: Literal["fixed"]


PercentileMethod = Literal["smallest_value_with_cumulative_volume_fraction_at_least_p"]


class Statistics(_Model):
    source: Literal["integration_points"]
    weighting: Literal["integration_point_volume"]
    ip_volume: Literal["jacobian_times_gauss_weight"]
    reported: tuple[Literal["mean", "p95"], ...]
    percentile: float = Field(gt=0, lt=100)
    percentile_method: PercentileMethod


class Sector(_Model):
    centre_deg: float = Field(ge=0, lt=360)
    half_width_deg: float = Field(gt=0, le=180)


SectorName = Literal["mesial", "distal", "buccal", "lingual"]


class CrestPlane(_Model):
    """The crest plane: at right angles to the implant (or tooth) axis, through the point where that axis meets the
    bone crest surface."""

    normal: Literal["implant_axis"]
    through: Literal["axis_crest_intersection"]


class CrestalRingRegion(_Model):
    kind: Literal["crestal_ring"]
    label: str
    material_group: str
    max_distance_from_surface_mm: float = Field(gt=0)
    max_depth_below_crest_mm: float = Field(gt=0)
    worse_side: Literal["highest_mean"]
    implant_surface: Literal["core_radius"]
    crest_plane: CrestPlane
    sector_boundary: Literal["first_included"]
    sector_direction: Literal["projected_to_occlusal_plane"]
    sectors: dict[SectorName, Sector]

    @model_validator(mode="after")
    def _sectors_cover_the_circle(self) -> CrestalRingRegion:
        if set(self.sectors) != {"mesial", "distal", "buccal", "lingual"}:
            raise ValueError("the crestal ring needs exactly the sectors mesial, distal, buccal and lingual")
        total = sum(2 * s.half_width_deg for s in self.sectors.values())
        if abs(total - 360.0) > 1e-9:
            raise ValueError(f"the sectors must cover 360 degrees exactly, not {total:g}")
        return self


class ComponentBandRegion(_Model):
    kind: Literal["component_band"]
    label: str
    component: str
    from_: Literal["platform"] = Field(alias="from")
    length_mm: float = Field(gt=0)


class ComponentRegion(_Model):
    kind: Literal["component"]
    label: str
    component: str


Region = Annotated[CrestalRingRegion | ComponentBandRegion | ComponentRegion, Field(discriminator="kind")]


class StrainMeasure(_Model):
    frost: Literal["von_mises_equivalent"]
    effective_poisson_ratio: Literal["material_nu"] | Annotated[float, Field(gt=-1, le=0.5)]
    """`material_nu`: each material's own Poisson's ratio, so ε_eq equals the axial strain under uniaxial stress."""
    formula: str = Field(min_length=10)
    stored: tuple[Literal["von_mises_equivalent", "max_principal", "min_principal"], ...]
    unit: Literal["microstrain"]


class FrostCategory(_Model):
    id: str
    label: str
    colour: str

    @field_validator("colour")
    @classmethod
    def _hex(cls, value: str) -> str:
        if not HEX_COLOUR.match(value):
            raise ValueError(f"colour must be #RRGGBB, not {value!r}")
        return value.upper()


class FrostScale(_Model):
    """Coloured by the measure `strain_measure.frost`."""

    label: str
    categories: tuple[FrostCategory, ...]
    edges: tuple[str, ...]

    @model_validator(mode="after")
    def _one_edge_between_each_pair(self) -> FrostScale:
        if len(self.categories) < 2:
            raise ValueError("the Frost scale needs at least two categories")
        if len(self.edges) != len(self.categories) - 1:
            raise ValueError("the Frost scale needs one edge (a library parameter key) between each pair of categories")
        if len({c.id for c in self.categories}) != len(self.categories):
            raise ValueError("Frost category ids must be unique")
        return self


class ContinuousScale(_Model):
    name: str
    stops: tuple[tuple[float, str], ...]

    @model_validator(mode="after")
    def _stops_span_zero_to_one(self) -> ContinuousScale:
        positions = [p for p, _ in self.stops]
        if len(positions) < 2 or positions[0] != 0.0 or positions[-1] != 1.0 or positions != sorted(positions):
            raise ValueError("continuous scale stops must rise from 0.0 to 1.0")
        for _, colour in self.stops:
            if not HEX_COLOUR.match(colour):
                raise ValueError(f"colour must be #RRGGBB, not {colour!r}")
        return self


MaterialClass = Literal["metal", "brittle", "bone", "not_compared"]


class StrengthRule(_Model):
    material_class: MaterialClass
    stress_measure: Literal["von_mises", "max_principal", "none"]
    reference: Literal["yield_strength", "flexural_strength", "none"]

    @model_validator(mode="after")
    def _measure_and_reference_together(self) -> StrengthRule:
        if (self.stress_measure == "none") != (self.reference == "none"):
            raise ValueError("a strength rule needs both a stress measure and a reference, or neither")
        return self


class KeyResult(_Model):
    """A result the mesh check compares between two detail levels (FR-037)."""

    region: str
    quantity: Literal["equivalent_strain", "percent_of_strength"]
    statistics: tuple[Literal["mean", "p95"], ...] = Field(min_length=1)
    per: str
    """`site`, `component`, or the component a region of these conventions is measured on (for example the one of
    the implant-neck band, for one value per implant). It is checked against the regions, not listed here, so that
    generic code names no block type (SC-012)."""


KEY_RESULT_GROUPINGS = ("site", "component")


class MeshCheck(_Model):
    limit: float = Field(gt=0, lt=1)
    key_results: tuple[KeyResult, ...] = Field(min_length=1)


class LibraryUnit(_Model):
    category: LibraryCategory
    pattern: str
    quantity: str
    units: str

    @field_validator("pattern")
    @classmethod
    def _pattern(cls, value: str) -> str:
        if not all(PATTERN_PART.match(part) for part in value.split(".")):
            raise ValueError(f"not a parameter-key pattern: {value!r}")
        return value


class Conventions(_Model):
    version: str
    units: Units
    frame: Frame
    load_angle: LoadAngle
    load_patch: LoadPatch
    analysis: Analysis
    detail_levels: DetailLevels
    statistics: Statistics
    regions: dict[str, Region]
    strain_measure: StrainMeasure
    frost_scale: FrostScale
    continuous_scale: ContinuousScale
    material_classes: dict[MaterialClass, tuple[str, ...]]
    strength_rules: tuple[StrengthRule, ...]
    mesh_check: MeshCheck
    equilibrium_tolerance: float = Field(gt=0, lt=1)
    moment_tolerance: float = Field(gt=0, lt=1)
    energy_tolerance: float = Field(gt=0, lt=1)
    residual_tolerance: float = Field(gt=0, lt=1)
    max_committed_asset_bytes: int = Field(gt=0)
    teaching_notice: str = Field(min_length=20)
    library_units: tuple[LibraryUnit, ...]

    @field_validator("version")
    @classmethod
    def _semver(cls, value: str) -> str:
        if not SEMVER.match(value):
            raise ValueError(f"version must be a semantic version such as 1.0.0, not {value!r}")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Conventions:
        materials = [m for members in self.material_classes.values() for m in members]
        if len(materials) != len(set(materials)):
            raise ValueError("a material may belong to only one material class")
        ruled = [r.material_class for r in self.strength_rules]
        if sorted(ruled) != sorted(set(ruled)) or set(ruled) != set(self.material_classes):
            raise ValueError("every material class needs exactly one strength rule")
        if "crestal_ring" not in self.regions:
            raise ValueError("the regions must include the crestal ring")
        unknown = sorted({k.region for k in self.mesh_check.key_results} - set(self.regions))
        if unknown:
            raise ValueError(f"mesh_check.key_results name regions that are not defined: {', '.join(unknown)}")
        components = {r.component for r in self.regions.values() if not isinstance(r, CrestalRingRegion)}
        groupings = sorted({k.per for k in self.mesh_check.key_results} - set(KEY_RESULT_GROUPINGS) - components)
        if groupings:
            allowed = ", ".join([*KEY_RESULT_GROUPINGS, *sorted(components)])
            raise ValueError(f"mesh_check.key_results group by unknown 'per' values {groupings}; use one of {allowed}")
        return self

    @property
    def mesh_check_limit(self) -> float:
        return self.mesh_check.limit

    def material_class_of(self, material: str) -> MaterialClass | None:
        for name, members in self.material_classes.items():
            if material in members:
                return name
        return None

    def strength_rule_for(self, material: str) -> StrengthRule | None:
        material_class = self.material_class_of(material)
        return next((r for r in self.strength_rules if r.material_class == material_class), None)

    def library_unit_for(self, category: str, parameter_key: str) -> LibraryUnit | None:
        """The first `library_units` row of `category` whose pattern matches `parameter_key`."""
        return next(
            (row for row in self.library_units if row.category == category and key_matches(row.pattern, parameter_key)),
            None,
        )


def is_parameter_key(value: str) -> bool:
    """A parameter key is dot-separated parts of letters, digits and underscores, e.g. `bone.cancellous.E.D4`."""
    return all(KEY_PART.match(part) for part in value.split("."))


def key_matches(pattern: str, parameter_key: str) -> bool:
    """`*` in a pattern stands for exactly one dot-separated part of the key."""
    parts, wanted = parameter_key.split("."), pattern.split(".")
    return len(parts) == len(wanted) and all(w == "*" or w == p for w, p in zip(wanted, parts, strict=True))
