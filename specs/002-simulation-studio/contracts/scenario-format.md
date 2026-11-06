# Contract: Scenario format and extension points

This document covers three things:

- the scenario definition the studio saves and the command line reads
- the contract a new **block type**, **template** or **physics capability** must follow. This is what lets FR-006
  and SC-012 hold: adding a block changes nothing elsewhere.

## Scenario definition (JSON)

```json
{
  "format": "dentalsim.scenario/1",
  "name": "Single implant crown, lower first molar",
  "conventions_version": "1.0.0",
  "detail_level": "standard",
  "blocks": [
    {"instance_id": "jaw", "type_id": "jaw_segment", "type_version": "1.0.0", "attach_to": null,
     "placement": {"position_mm": [0, 0, 0], "axis_angles_deg": {"mesiodistal": 0, "buccolingual": 0}, "rotation_deg": 0},
     "settings": {
       "jaw": {"kind": "design", "value": "mandible"},
       "region": {"kind": "design", "value": ["36"]},
       "site": {"kind": "design", "value": "36"},
       "neighbours": {"kind": "design", "value": ["35", "37"]},
       "density_class": {"kind": "design", "value": "D2"},
       "cortical_thickness": {"kind": "library", "entry_id": "01J...", "entry_version": 2, "choice": "typical", "value": 2.0},
       "cortical_E": {"kind": "library", "entry_id": "01J...", "entry_version": 1, "choice": "source:01J...", "value": 13700}
     }},
    {"instance_id": "implant-1", "type_id": "implant", "type_version": "1.0.0",
     "attach_to": {"instance_id": "jaw", "attach_point": "site"},
     "placement": {"position_mm": [0, 0, 0], "axis_angles_deg": {"mesiodistal": 0, "buccolingual": 0}, "rotation_deg": 0},
     "settings": {"diameter_mm": {"kind": "design", "value": 4.1}, "length_mm": {"kind": "design", "value": 10},
                  "thread": {"kind": "design", "value": "v_thread"}, "healing_state": {"kind": "design", "value": "osseointegrated"}}}
  ],
  "loads": [
    {"id": "bite-1", "pattern": "single_bite",
     "force": {"kind": "library", "entry_id": "01J...", "entry_version": 1, "choice": "typical", "value": 200},
     "direction": {"angle_deg": 30, "azimuth_deg": 90},
     "contact_points": [{"block": "crown-1", "point": "buccal_cusp", "share": 1.0}]}
  ]
}
```

**Rules**:

- Unknown fields are refused, and there are no patient fields (FR-048).
- A library setting must carry `entry_id` and `entry_version`.
- A value outside the published range sets `outside_range: true` (FR-052).
- `type_version` is pinned per block instance, so an updated block type never silently changes a saved scenario.
  Opening the scenario offers to upgrade.

## Block type contract

Each block type is one Python module under `engine/src/dentalsim/blocks/` and registers a `BlockType` with:

| Member | Must provide |
|---|---|
| `type_id`, `version`, `slice` | Identity and delivery slice |
| `settings` | Pydantic model. Each field is annotated with `group` (one of 5) and `kind` (`library` with a `parameter_key`, or `design` with limits). |
| `attach_points` | Named sockets it offers and the sockets it can plug into |
| `needs_physics` | Capability ids. Gating reads only this list. |
| `result_types` | Result type ids, each with its own `needs_physics` (for example `screw_stress` needs `screw_preload`) |
| `report_regions` | Region ids from the conventions document |
| `build_geometry(ctx) -> Parts` | gmsh OpenCASCADE solids with material tags. No meshing, no solver calls. |
| `keep_out(ctx) -> Volumes` | Spaces others may not enter, such as the nerve canal, the sinus or a root envelope. Used by the validity checks. |
| `check(ctx) -> list[Problem]` | Block-specific validity rules, for example an implant longer than the available bone |
| `occlusal_points(ctx)` | Named contact points, if it carries load |
| `joints(ctx)` | Contact areas where connectors can join it to its neighbours, if it is a prosthesis unit (slice 1b): position, outward normal, and whether the unit stands on its own support (`carried`: a crown on its abutment, not a pontic) |
| `links(ctx)` | The units it joins, if it is a connector, found among the other blocks' joints (slice 1b) |

**Sockets.** A block attaches to a socket of its parent by name (`attach_to.attach_point`). An attach point may be
**keyed** (`keys_from`, a string-list design setting): it then gives one socket per value, named `<name>:<value>`.
The jaw segment offers `site:<FDI>` for each tooth of its region, where implants, pontics and cantilever extensions
sit; a bare `site` means its reference site (`default_key_from`), so slice 1a scenarios keep their meaning. Each
socket holds at most its attach point's `capacity` blocks.

**Placement is not support** (slice 1b). The tree says where a block sits; what holds it up is the joined
geometry. A pontic sits at a jaw site but rests on nothing there; the splinting connectors that join it to the units
beside it hold it. Blocks see every unit's joints and every connector's links through their context's environment,
so a pontic or cantilever extension can report `unsupported_pontic` while the owner builds.

**Rules**:

- Generic code never imports a specific block. This covers the run library, comparison, studies, exports and
  publishing: they all work on Parts, regions, result types and key numbers.
- A new block must ship with unit tests for its geometry (volume, bounding box, attach positions) and its
  validity rules.

## Template contract

A built-in template is a YAML file under `engine/src/dentalsim/templates/` containing:

- `prosthesis_type`
- `branch`
- `fit_rules` (the jaw-segment conditions it needs)
- `blocks`, written in the scenario format with design defaults and library parameter keys; the typical approved
  version is resolved when the template is opened

The template is delivered in the same slice as its last-needed block.

## Physics capability contract

A capability is registered in `engine/src/dentalsim/physics/` with:

| Member | Must provide |
|---|---|
| `id`, `linear` | Identity; whether superposition is allowed |
| `required_cases` | Verification case ids that gate it |
| `write_solver_input(model, solver)` | Adds its cards to the solver input, for example contact pairs or `*PRE-TENSION SECTION` |
| `post(results)` | Any result fields it adds, for example micromotion |

**Rules**:

- Solver specifics live only in `engine/src/dentalsim/solvers/<solver>/`. A capability may support more than one
  solver adapter, for example CalculiX now and FEBio later (R3).
- A capability with `status != ready` makes the gate refuse previews and runs of any scenario whose blocks need it,
  naming the capability (FR-011a). Result types that need it are listed in `locked_results` instead of failing the
  run.
