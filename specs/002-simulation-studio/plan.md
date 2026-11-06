# Implementation Plan: Simulation Studio

**Branch**: `002-simulation-studio` | **Date**: 2026-11-05 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/002-simulation-studio/spec.md`

## Summary

The owner gets a local studio for building, running, checking and publishing finite element scenarios.

- **How it is used**: it runs on the owner's main PC and opens in a browser on either home computer.
- **Building**: scenarios are assemblies of building blocks on a jaw segment, and every material, bone, load and
  strength value comes from an owner-approved literature library.
- **Engine**: a new Python engine, `dentalsim`:
  - generates the shapes and a matching mesh with **gmsh**
  - solves with **CalculiX** (open source; the Windows build with the open-source PaStiX solver)
  - reports region means and 95th percentiles under one conventions document
- **Speed**: linear load cases are solved once as unit loads and combined. One run therefore covers every bite
  angle and force, and interactive page data needs no interpolation for those controls.
- **Studio**: a **FastAPI** server and a separate worker process queue previews and heavy runs. Previews take 10 s
  or less; detailed runs take 30 min or less.
- **Interface**: a **React + three.js** browser interface for the builder, results, comparisons, library,
  verification, exports and publishing.
- **Delivered here**: Stories 1–8 on bonded-elastic physics (slices 1a and 1b), plus the full framework that
  locks and unlocks physics.
- **Later increments**: Story 9's physics (screw preload, ligament, contact, fatigue), each planned on its own
  after its benchmark research ([research.md](research.md) R21).

## Technical Context

| Item | Choice |
|---|---|
| **Language/Version** | Python 3.12 (engine, server, worker, command line), managed with uv; TypeScript 5 with Node 24 LTS for the browser interface (build only) |
| **Primary Dependencies** | gmsh 4.15 (OpenCASCADE geometry and meshing); CalculiX ccx 2.23, the open-source `ccx_static` build with the PaStiX solver (with Scotch, hwloc and OpenBLAS; research R3); NumPy, SciPy; pydantic 2; FastAPI with uvicorn; argon2-cffi; psutil; PyVista/VTK (figures and films); imageio-ffmpeg (H.264); python-pptx; typst (PDF reports); openpyxl; Pillow; Kokoro-82M (promo voice, Apache-2.0); scikit-fem (independent cross-check for the textbook benchmarks only). Interface: React 19, Vite, Mantine, TanStack Query, three.js with react-three-fiber. |
| **Storage** | SQLite (WAL, FTS5) in the data folder `D:\DentalSimData` as the system of record. Content-addressed results store (zstd). Repository files for conventions, verification cases, library proposals and snapshots, and published packages. Large published media are named by SHA-256, outside git. |
| **Testing** | pytest (markers `unit`, `integration`, `benchmark`, `regression`), Vitest, Playwright smoke tests; `dentalsim check` is the merge gate, and only a complete run (benchmark and regression tests included, so on the main PC) passes it; a run without the solver is "incomplete" and blocks merging |
| **Target Platform** | Windows 11. The main PC (RTX 4060 Ti, 10 physical cores, about 32 GB) runs the server, worker and solver; the laptop (24 GB) is a browser client and the development machine. Clients are current Chrome or Edge. Optionally, a rented solver machine (Windows or Linux, reached over SSH) solves heavy runs that do not fit the main PC (FR-064, research R23). |
| **Project Type** | Local web application (Python server and worker, plus a browser interface) on a shared engine library with a command line |
| **Performance Goals** | Preview ≤ 10 s after a change (SC-002); detailed single implant crown ≤ 30 min (SC-003); 18 detailed runs ≤ 10 h overnight (SC-004); scenarios with up to 3 implants or teeth ≤ 10 h (SC-013); comparison ready in < 1 min (SC-008); key numbers reproduce within 0.1% (SC-007) |
| **Constraints** | Home network only, with owner password and HTTPS (FR-001); one heavy run at a time; memory refusal above 85% of the solving machine's RAM, watchdog at 92%; every unit case passes the force, energy and residual checks (FR-023); no patient data fields; open-source tools only; runs from uncommitted engine code are never publishable |
| **Scale/Scope** | One user. About 30 published scenarios and hundreds to low thousands of runs in year one. Detailed models of about 0.3–1.2 M degrees of freedom on the main PC (its memory limit, measured in research R8); larger ones need a lower detail level, a factor-once solver build (R4) or the rented solver machine (R23). Previews about 15–25 k degrees of freedom (R7). 5 capabilities, about 17 block types and 8 templates over all slices. About 12 interface screens. |

There are no NEEDS CLARIFICATION items; every choice is resolved in [research.md](research.md).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design (below).*

| Principle | How this plan complies | Status |
|---|---|---|
| **I. Teaching, Not Prediction** | No patient fields anywhere; schemas refuse unknown fields (FR-048). 3D import is out of scope. Every result view and export template carries the teaching-only notice (FR-028), and it is a required package field. Frost windows and strength references are labelled "teaching reference" with their source. | ✅ |
| **II. Verified Before Published** | Verification suite: textbook cases plus 3–5 published studies, with tolerances fixed in git before the first run (R15). Publishing stays locked until every required case passes (FR-036). Mesh check, recorded convergence, verification note and owner sign-off are checklist items. Only region means and 95th percentiles from integration points; no peaks (R5). | ✅ |
| **III. Transparent Assumptions** | The assumptions note with FR-061 fields is required for publishing. Every library value carries its sources. The package contract carries assumptions and references. | ✅ |
| **IV. One Shared Physics Convention** | One file, `docs/conventions.md`, with a machine-readable block. The engine refuses to start without it. Tests cover it, and a change of version flags every dependent run and package (R6, FR-051). | ✅ |
| **V. Solve Offline, Deliver Instantly** | Every solve runs on the main PC. The package delivers precomputed glTF fields and solved cases; superposition and lookup in the browser are arithmetic on stored results, not solves (R18). | ✅ |
| **VI. Reproducible Pipeline** | Every output is regenerated from stored results, the run record and a template version, through the studio or `dentalsim` (FR-044). Input-hash keys cover a hash of the result-affecting engine source (not the repository commit, so interface, server or documentation commits never force a re-solve), solver build, gmsh version, conventions and library versions (R14). The commit is kept for provenance. Builds from uncommitted engine code are blocked from publishing. | ✅ |
| **VII. Honest Visuals** | Shared colour and deformation scales are enforced in comparisons and packages. Magnification is stated. Frame review on desktop and phone is a checklist item. Promo clips come only from published scenarios. Brand names are blocked unless the brand-comparison fields are filled in. | ✅ |
| **VIII. Literature-Sourced Inputs** | Library settings must point to an approved, immutable entry version. The source check passes before approval. Ranges are shown. Runs record entry versions and hashes. Rejecting an entry flags the runs that used it. | ✅ |
| **Scope: open source, compute, git size** | All tools are open source, including the solver build (PaStiX in `ccx_static.exe`; PARDISO was dropped because Intel MKL is not open source, R3). GPL tools run as separate processes; outputs are unencumbered. Compute is on the main PC; a rented solver machine, when used, follows constitution 2.1.0 (inputs only, key-authenticated SSH, verified build, nothing kept there; R23). Results and large media stay outside git (R12, R19). | ✅ |
| **Scope: new physics needs a benchmark first** | The capability gate (FR-011a, FR-062) also covers bonded elastic itself. | ✅ |
| **Workflow: tests, gates** | `dentalsim check` is the merge gate; only a complete run on the main PC, with benchmark and regression tests, passes it ("incomplete" blocks merging). Every new solver capability ships with benchmark tests. | ✅ |

**Result**: PASS, with no violations, so Complexity Tracking is empty.

## Project Structure

### Documentation (this feature)

```text
specs/002-simulation-studio/
├── plan.md              # This file
├── research.md          # Phase 0: decisions R1–R22
├── data-model.md        # Phase 1: entities, fields, states
├── quickstart.md        # Phase 1: setup and per-story checks
├── contracts/
│   ├── studio-api.md            # HTTP API used by the browser interface
│   ├── cli.md                   # dentalsim command line (Principle VI)
│   ├── scenario-format.md       # scenario JSON; block, template and physics extension contracts
│   ├── library-entry-format.md  # YAML proposals and snapshots
│   └── published-package.md     # studio → website contract (FR-046, FR-063)
├── checklists/requirements.md
└── tasks.md             # Phase 2 (/speckit-tasks; not created here)
```

### Source Code (repository root)

```text
pyproject.toml                 # uv workspace: engine + studio/server
uv.lock
docs/
└── conventions.md             # the conventions document (FR-050), prose + ```yaml conventions``` block

engine/                        # Python package "dentalsim": the shared engine and command line
├── pyproject.toml
├── src/dentalsim/
│   ├── conventions/           # parse and validate docs/conventions.md; units, frame, regions, colour scales
│   ├── model/                 # pydantic models: Scenario, BlockInstance, SettingValue, LoadCase
│   ├── blocks/                # one module per block type (jaw_segment, implant, abutment, crown, pontic, ...)
│   ├── templates/             # built-in prosthesis templates (YAML) + loader and fit rules
│   ├── assembly/              # placement, attach points, keep-out volumes, collision and validity checks
│   ├── geometry/              # gmsh OpenCASCADE builders, fragments, material tags
│   ├── mesh/                  # detail levels, size fields, quality checks, DOF prediction
│   ├── physics/               # capability registry, gating, benchmark status
│   ├── solvers/calculix/      # .inp writer, process runner, .frd/.dat readers, solver build id
│   ├── solvers/skfem/         # independent cross-check solver for textbook cases
│   ├── post/                  # superposition, integration-point fields, regions, statistics, Frost, % strength
│   ├── library/               # entries, versions, source check (Crossref, NCBI), YAML import and export
│   ├── store/                 # input hashing, content-addressed store, out-of-date detection
│   ├── estimate/              # time, memory and disk models (R8)
│   ├── verification/          # case runner, reports, mesh check, preview accuracy
│   ├── exports/               # figures, films, slide loops, pptx, report (typst), spreadsheet, promo
│   ├── publish/               # checklist, package builder, interactive page data
│   └── cli.py                 # "dentalsim" command
└── tests/
    ├── unit/  integration/  benchmark/  regression/

studio/
├── server/                    # Python package "dentalsim_studio"
│   ├── src/dentalsim_studio/
│   │   ├── api/               # FastAPI routers (contracts/studio-api.md)
│   │   ├── auth/              # password, sessions, home-network guard, certificate
│   │   ├── db/                # SQLite schema, migrations, FTS5 search, backup
│   │   ├── jobs/              # queue, lanes, restart recovery
│   │   ├── worker/            # worker process: runs engine jobs, watchdog, sleep prevention
│   │   └── events/            # Server-Sent Events
│   └── tests/
└── ui/                        # React + TypeScript + Vite
    ├── src/
    │   ├── pages/             # Builder, Results, Compare, Projects, Queue, Library, Verification, Exports, Publish
    │   ├── viewer/            # three.js scene, glTF loader, colour scales (generated from conventions)
    │   ├── components/
    │   └── api/
    └── tests/

content/
├── library/proposals/         # YAML proposals (seed library for bonded-elastic blocks)
├── library/snapshots/         # exported approved snapshots
├── verification/cases/        # case files (quantity, expected value, tolerance) committed before first run
└── scenarios/                 # published packages: <slug>/v<N>/package.json + small assets

web/                           # feature 001, later; reads content/scenarios
fea-implant-teaching/          # old project, reference and benchmarks only (not imported by the engine)
```

**Structure decision**: a monorepo with one shared engine (`engine/`) that both the studio (`studio/`) and the
command line use. The studio is a thin layer for HTTP, the queue and the interface. All science lives in the
engine, so the command line and the studio give identical results (Principle VI). The website (`web/`) will read
only `content/scenarios/`.

## Delivery stages (order for /speckit-tasks)

| Stage | Contents | Gate to move on |
|---|---|---|
| **0. Foundations** | uv workspace, solver acquisition and `dentalsim solver check` on the main PC, calibration runs (R3, R8); conventions document drafted and approved by the owner (FR-050); data folder, database, store, hashing; server skeleton with password, home-network guard and HTTPS; worker and lanes | `conventions check` passes; solver smoke test passes |
| **1a. Single implant crown** | Seed library proposals and the approval flow (minimum of Story 2); bonded-elastic textbook benchmarks and capability gate; jaw segment, implant, abutment and crown blocks; template; validity checks; preview; estimate; detailed run; results views | Story 1 checks for 1a pass; preview ≤ 10 s; detailed ≤ 30 min |
| **1b. Multi-implant fixed** | Pontic, splinting connector and cantilever blocks; splinted, bridge, cantilever and full-arch fixed templates | Story 1 checks for 1b pass; 3-implant scenario ≤ 10 h |
| **2–4** | Full library (bulk import, edit versions, rejection flags); projects, search, duplicate-and-change, comparison; studies, sensitivity, restart recovery | Stories 2–4 checks |
| **5** | Owner chooses 3–5 published studies; case files committed; reproductions; mesh check; preview accuracy | SC-006 |
| **6–8** | Exports, publishing and package, interactive data, promo clips | Stories 6–8 checks; first scenario published |
| **9 (later increments)** | Screw preload → ligament → contact → fatigue, each with its own benchmarks, library entries, blocks and templates | Each capability's benchmark |

## Key design notes

- **Physics gating** (FR-011a): the gate reads only the `needs_physics` lists of the blocks and result types and
  each capability's status. Bonded elastic starts as "awaiting benchmark", so slice 1a's first run is impossible
  until the textbook cases pass.
- **Preview accuracy** (FR-017): the verification runner solves every case at the preview level too, and stores
  the worst difference that previews display.
- **Regions** are geometric tests in the conventions document, so they are identical across detail levels. This
  is what makes the mesh check meaningful.
- **Out of date** (FR-058): the hashes are compared each time a stored item is opened; nothing is reused silently.
- **No block-specific code outside `blocks/` and `templates/`**: this is checked by an import-boundary test, so
  SC-012 holds.
- **Convergence checks** (FR-023): the solver never reports a singular matrix (R3), so the engine checks every
  unit case itself: force balance, energy (F · u against twice the strain energy) and the PaStiX residual, plus a
  connectivity check of the mesh before the solve.
- **Rented solver machine** (FR-064, R23): only the ccx step can move to it, over SSH; it is a solver build of
  its own, verified and calibrated before use. Previews never leave the main PC.

## Complexity Tracking

There are no constitution violations to justify.

## Post-design Constitution Check (after Phase 1)

The data model, contracts and quickstart were re-checked against Principles I–VIII:

- The package contract requires the notice, statistics labels, shared scales, references and provenance.
- The data model has no patient fields and blocks publication on any blocking flag.
- The command-line contract gives a non-studio route for every output.

**Result**: still PASS.
