# Conventions

This is the project's single conventions document (constitution, Principle IV; spec FR-050). Every block, result,
export and published package follows it.

It has two parts:

- **Prose** for the owner and for readers of the assumptions notes.
- **One machine-readable block**, the fenced `yaml conventions` block at the end. The engine reads only that
  block. If it is missing or invalid, nothing runs (`dentalsim conventions check` reports why).

Changing any value in the block needs a new `version`. A new version flags every stored run and published
scenario made under an older one for re-verification (FR-051).

> **Status**: draft 1.0.0, proposed by Claude on 2026-11-05. The owner reviews and approves it in task T022; until
> then the values below are proposals.

## Units

| Quantity | Unit |
|---|---|
| Length | mm |
| Force | N |
| Stress, elastic modulus | MPa (N/mm²) |
| Mass | tonne (t), so that N = t·mm/s² |
| Time | s |
| Strain | reported in microstrain (µε = 10⁻⁶) |

Literature entries use the units in the [library units table](#library-units). A modulus is always in MPa, so a
paper's 13.7 GPa is entered as 13 700 MPa.

## Coordinate frame

- **Origin**: the crest centre of the first implant or tooth site.
- **+z**: occlusal, along the long axis of the reference implant (the first implant, or the first tooth).
- **+x**: mesial.
- **+y**: buccal (labial in the anterior region).
- The frame is right-handed: x × y = z.

All placements, contact points and reported locations are given in this frame, in mm.

## Load direction and the load-angle sign

A load has a magnitude (from the literature library) and a direction given by two design settings:

- **Angle** (`angle_deg`): measured from the axial bite. 0° is a purely axial force, pushing toward apical
  (along −z).
- **Azimuth** (`azimuth_deg`): the side the load comes from, measured in the occlusal plane from +x (mesial)
  toward +y (buccal). 90° means the load comes from the buccal side, which is the usual oblique bite on a buccal
  cusp. The default is 90°.
- **Sign**: a positive angle tilts the force away from the azimuth side. With the default azimuth of 90°, +30°
  is a force tilted 30° toward lingual (palatal in the maxilla).

As a formula, for a force of magnitude F, angle a and azimuth b:

F = F · (−sin a · cos b, −sin a · sin b, −cos a)

## Load patch

A force acts on a small patch of the occlusal surface, not on one node, so that no artificial point-load
singularity is created. The patch is a disc of radius `load_patch_radius_mm` (proposed: 1.0 mm) around the
contact point on the outer surface; the force is shared over the patch nodes.

## Regions used for reported values

Reported values are computed at integration points, weighted by integration-point volume, and summarised as the
region **mean** and the volume-weighted **95th percentile** (constitution, Principle II). Singular corner peaks and
nodal maxima are never reported.

Regions are geometric tests on integration-point positions plus the material or component of the element. They
are not mesh partitions, so the same region exists at every detail level, which the mesh check needs.

| Region | Definition |
|---|---|
| **Crestal ring** (`crestal_ring`) | Cortical bone within 1.5 mm of the implant (or root) surface and no deeper than 2.0 mm below the crest, around each implant or tooth. The implant surface is taken at the core (minor) radius of the threads, so bone between the threads belongs to the ring at every detail level. Depth is measured along the implant axis, from the crest taken as a plane at right angles to that axis. |
| **Implant neck** (`implant_neck`) | The top 2.0 mm of the implant body, measured from the implant platform along its axis. |
| **Whole components** | The whole implant (`implant`), abutment (`abutment`), retention screw (`screw`), crown (`crown`), pontic (`pontic`), splinting connector (`connector`) and cantilever extension (`cantilever`). |

**The worse side.** Around each implant or tooth, the crestal ring is split into four sectors by the direction of
each integration point from the implant axis, seen from +z:

| Sector | Centre direction | Width |
|---|---|---|
| Mesial | +x (0°) | ±45° |
| Buccal | +y (90°) | ±45° |
| Distal | −x (180°) | ±45° |
| Lingual | −y (270°) | ±45° |

Each sector includes its first boundary and excludes its second (45° is buccal, 315° is mesial). For a tilted
implant the direction is taken after projecting onto the occlusal (xy) plane.

The sector with the highest mean strain is the worse side. It is reported and named, for example "crestal strain,
distal side, mean 1 240 µε".

## Bone strain and the Frost windows

- **Measure**: the von Mises equivalent strain, in µε, is what the Frost colours show. The maximum and minimum
  principal strains are also stored.
- **Definition**: ε_eq = 1/(1 + ν′) · √(½ [(ε₁ − ε₂)² + (ε₂ − ε₃)² + (ε₃ − ε₁)²]), with ν′ = 0.5. This equals
  √(⅔ e : e), where e is the deviatoric strain. *Note for the owner*: some commercial codes report an "equivalent
  elastic strain" with ν′ set to the material's own Poisson's ratio, which gives larger values for bone
  (ν ≈ 0.3). Published strain values must be compared with that difference in mind.
- **Frost scale**: four categories, labelled **teaching reference** wherever they are shown, with their source.

  | Category | Colour |
  |---|---|
  | Disuse | sky blue `#56B4E9` |
  | Adapted window | bluish green `#009E73` |
  | Mild overload | orange `#E69F00` |
  | Pathological overload | vermillion `#D55E00` |

  The colours are from the Okabe–Ito palette, which stays distinguishable with the common forms of colour
  blindness.
- **The window edges are not in this document.** Each edge is a literature-library entry (`frost.disuse_upper`,
  `frost.adapted_upper`, `frost.mild_overload_upper`), so it carries its sources and the owner approves it like any
  other value (constitution, Principle VIII).

## Stress compared with strength

Component stress is reported as a percentage of the material's strength reference, as the region mean and the
95th percentile, with the reference's source and the label **teaching reference**.

| Material class | Materials | Stress measure | Compared with |
|---|---|---|---|
| Metals | titanium grade 4, titanium grade 5 (Ti-6Al-4V), cobalt-chromium | von Mises stress | Yield strength (`material.<id>.yield_strength`) |
| Brittle materials | zirconia, lithium disilicate, feldspathic porcelain, resin composite | Maximum principal (tensile) stress | Flexural strength (`material.<id>.flexural_strength`) |
| Bone | cortical and cancellous bone | Strain, as above | Not compared with a strength |
| Not compared | resin cement | — | Not compared |

## Mesh check and convergence checks

- **Mesh-check limit**: a scenario's mesh is justified when every key result changes by no more than 5% between
  two detail levels (FR-037).
- **Convergence checks** (FR-023), after every solve and for each unit load case. Any one failing fails the run
  with that reason:
  - **Force balance**: the sum of the support reactions against the applied load. Relative error at most
    1 × 10⁻⁶.
  - **Energy check**: the work of the applied load (F · u) against twice the strain energy (summed at the
    integration points). For a correct linear solution they are equal; a part that is not fully supported can
    still balance its forces but breaks this. Relative error at most 1 × 10⁻⁴ (the displacements in the solver's
    result file carry 6 significant digits).
  - **Solver residual**: where the equation solver reports one (PaStiX does), at most 1 × 10⁻¹⁰. PaStiX
    refines to below 1 × 10⁻¹²; a value far above that means the stiffness matrix is singular or nearly so.

## Published assets in git

A published asset larger than 1 MB goes to the published-media folder, named by its SHA-256, instead of git
(constitution, Scope).

## Teaching-only notice

Every result view, export and published page carries this notice (FR-028):

> For teaching only. These results come from a generic model, not from any patient, and are not a prediction for
> any patient.

## Library units

Every literature entry's units must match the row of this table that its parameter key matches. In a pattern,
`*` stands for exactly one dot-separated part of the key.

| Category | Parameter key | Quantity | Units |
|---|---|---|---|
| material | `material.*.E` | Elastic modulus | MPa |
| material | `material.*.nu` | Poisson's ratio | 1 (no unit) |
| strength | `material.*.yield_strength` | Yield strength (0.2% proof stress) | MPa |
| strength | `material.*.flexural_strength` | Flexural strength | MPa |
| bone | `bone.*.E` | Elastic modulus | MPa |
| bone | `bone.*.E.*` | Elastic modulus by density class | MPa |
| bone | `bone.*.nu` | Poisson's ratio | 1 (no unit) |
| bone | `bone.cortical.thickness.*` | Cortical thickness by density class | mm |
| load | `load.bite_force.*` | Bite force | N |
| load | `load.clenching_force` | Clenching or bruxism force | N |
| load | `load.chewing_cycles_per_year` | Chewing cycles per year | 1/year |
| frost_window | `frost.*` | Frost window edge | microstrain |

## Change log

| Version | Date | Change | Approved by the owner |
|---|---|---|---|
| 1.0.0 | 2026-11-05 | First draft, proposed by Claude; energy and residual tolerances added the same day, before approval and before any run | *pending (T022)* |

## Machine-readable block

```yaml conventions
version: 1.0.0

units:
  length: mm
  force: N
  stress: MPa
  mass: tonne
  time: s
  strain: microstrain

frame:
  origin: crest centre of the first implant or tooth site
  handedness: right
  axes:
    x: {direction: mesial, vector: [1, 0, 0]}
    y: {direction: buccal, vector: [0, 1, 0]}
    z: {direction: occlusal, vector: [0, 0, 1]}

load_angle:
  zero: axial
  azimuth_reference: mesial
  azimuth_toward: buccal
  positive_tilt: away_from_azimuth
  default_azimuth_deg: 90

load_patch_radius_mm: 1.0

statistics:
  weighting: integration_point_volume
  reported: [mean, p95]
  percentile: 95

regions:
  crestal_ring:
    kind: crestal_ring
    label: Crestal bone ring
    material_group: cortical_bone
    max_distance_from_surface_mm: 1.5
    max_depth_below_crest_mm: 2.0
    worse_side: highest_mean
    sectors:
      mesial: {centre_deg: 0, half_width_deg: 45}
      buccal: {centre_deg: 90, half_width_deg: 45}
      distal: {centre_deg: 180, half_width_deg: 45}
      lingual: {centre_deg: 270, half_width_deg: 45}
  implant_neck:
    kind: component_band
    label: Implant neck
    component: implant
    from: platform
    length_mm: 2.0
  implant: {kind: component, label: Implant, component: implant}
  abutment: {kind: component, label: Abutment, component: abutment}
  screw: {kind: component, label: Retention screw, component: screw}
  crown: {kind: component, label: Crown, component: crown}
  pontic: {kind: component, label: Pontic, component: pontic}
  connector: {kind: component, label: Splinting connector, component: connector}
  cantilever: {kind: component, label: Cantilever extension, component: cantilever}

strain_measure:
  frost: von_mises_equivalent
  effective_poisson_ratio: 0.5
  stored: [von_mises_equivalent, max_principal, min_principal]
  unit: microstrain

frost_scale:
  label: teaching reference
  measure: von_mises_equivalent
  categories:
    - {id: disuse, label: Disuse, colour: "#56B4E9"}
    - {id: adapted, label: Adapted window, colour: "#009E73"}
    - {id: mild_overload, label: Mild overload, colour: "#E69F00"}
    - {id: pathological_overload, label: Pathological overload, colour: "#D55E00"}
  edges: [frost.disuse_upper, frost.adapted_upper, frost.mild_overload_upper]

continuous_scale:
  name: viridis
  stops:
    - [0.0, "#440154"]
    - [0.125, "#472D7B"]
    - [0.25, "#3B528B"]
    - [0.375, "#2C728E"]
    - [0.5, "#21918C"]
    - [0.625, "#28AE80"]
    - [0.75, "#5EC962"]
    - [0.875, "#ADDC30"]
    - [1.0, "#FDE725"]

material_classes:
  metal: [titanium_g4, titanium_g5, cocr]
  brittle: [zirconia, lithium_disilicate, feldspathic, composite]
  bone: [cortical_bone, cancellous_bone]
  not_compared: [resin_cement]

strength_rules:
  - {material_class: metal, stress_measure: von_mises, reference: yield_strength}
  - {material_class: brittle, stress_measure: max_principal, reference: flexural_strength}
  - {material_class: bone, stress_measure: none, reference: none}
  - {material_class: not_compared, stress_measure: none, reference: none}

mesh_check_limit: 0.05
equilibrium_tolerance: 1.0e-6
energy_tolerance: 1.0e-4
residual_tolerance: 1.0e-10
max_committed_asset_bytes: 1048576

teaching_notice: >-
  For teaching only. These results come from a generic model, not from any patient,
  and are not a prediction for any patient.

library_units:
  - {category: material, pattern: "material.*.E", quantity: elastic modulus, units: MPa}
  - {category: material, pattern: "material.*.nu", quantity: Poisson's ratio, units: "1"}
  - {category: strength, pattern: "material.*.yield_strength", quantity: yield strength, units: MPa}
  - {category: strength, pattern: "material.*.flexural_strength", quantity: flexural strength, units: MPa}
  - {category: bone, pattern: "bone.*.E", quantity: elastic modulus, units: MPa}
  - {category: bone, pattern: "bone.*.E.*", quantity: elastic modulus by density class, units: MPa}
  - {category: bone, pattern: "bone.*.nu", quantity: Poisson's ratio, units: "1"}
  - {category: bone, pattern: "bone.cortical.thickness.*", quantity: cortical thickness by density class, units: mm}
  - {category: load, pattern: "load.bite_force.*", quantity: bite force, units: N}
  - {category: load, pattern: "load.clenching_force", quantity: clenching or bruxism force, units: N}
  - {category: load, pattern: "load.chewing_cycles_per_year", quantity: chewing cycles per year, units: 1/year}
  - {category: frost_window, pattern: "frost.*", quantity: Frost window edge, units: microstrain}
```
