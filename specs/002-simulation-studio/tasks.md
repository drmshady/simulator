---

description: "Task list for feature 002: Simulation Studio"
---

# Tasks: Simulation Studio

**Input**: Design documents from `specs/002-simulation-studio/`
**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md),
[contracts/](contracts/), [quickstart.md](quickstart.md)

**Tests**: included only where the constitution or a contract requires them:

- the conventions
- the engine benchmark suite (every solver capability)
- regression tests for published reference numbers
- unit tests for each block's geometry and validity rules (scenario-format contract)
- the import-boundary test (SC-012)
- the package-contract test
- the key refusal paths

Testing is regression-based, not strictly test-first (constitution, Development Workflow), so test tasks come after
the code they cover within each phase.

**Organization**: tasks are grouped by user story. User Story 1 has two phases, one per delivery slice (1a, then 1b).
Story 9's physics are separate later features (research R21). Only the lock and unlock framework that needs
publishing is tasked here.

**Who does a task**: tasks without a prefix are coding tasks. **Owner** tasks need the owner (approval or a check on
the main PC). **Claude research** tasks are literature work that the owner then approves.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel with the other [P] tasks next to it (different files, no shared unfinished dependency)
- **[Story]**: the user story the task belongs to (US1 … US9)
- **Command-line commands** each live in their own module, `engine/src/dentalsim/commands/<name>.py`, registered with
  `engine/src/dentalsim/cli.py`. This keeps parallel tasks from editing the same file.
- **API routers and worker job handlers** are likewise one file each.

## Path conventions (from plan.md)

| Path | Contents |
|---|---|
| `engine/src/dentalsim/` | Engine and command line (package `dentalsim`); tests in `engine/tests/{unit,integration,benchmark,regression}/` |
| `studio/server/src/dentalsim_studio/` | Server, database, queue and worker; tests in `studio/server/tests/` |
| `studio/ui/src/` | Browser interface; tests in `studio/ui/tests/` |
| `content/` | Library proposals and snapshots, verification cases, published packages |
| `docs/conventions.md` | The conventions document |
| `D:\DentalSimData` | Data folder (`DENTALSIM_DATA`) |

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create the monorepo, toolchains and configuration.

- [X] T001 Create the monorepo skeleton from plan.md:
  - `engine/src/dentalsim/`, with one subpackage (each with `__init__.py`) for each of: `conventions`, `model`,
    `blocks`, `templates`, `assembly`, `geometry`, `mesh`, `physics`, `solvers/calculix`, `solvers/skfem`, `post`,
    `library`, `store`, `estimate`, `verification`, `exports`, `publish`, `commands`
  - `engine/tests/{unit,integration,benchmark,regression}/`
  - `studio/server/src/dentalsim_studio/{api,auth,db,db/repos,jobs,worker,worker/handlers,events}/`
  - `studio/server/tests/`
  - `content/library/{proposals,snapshots}/`, `content/verification/cases/` and `content/scenarios/`, each with a
    `.gitkeep`
  - `docs/setup/`
- [X] T002 [P] Create `engine/pyproject.toml` for the package `dentalsim`:
  - Python 3.12
  - dependencies: gmsh 4.15.x, numpy, scipy, pydantic 2, pyyaml, python-ulid, zstandard, httpx, psutil, pyvista,
    imageio-ffmpeg, python-pptx, typst, openpyxl, pillow, pygltflib, scikit-fem
  - optional extra `promo`: kokoro, soundfile
  - console script `dentalsim = dentalsim.cli:main`
- [X] T003 [P] Create `studio/server/pyproject.toml` for the package `dentalsim_studio`: fastapi, uvicorn[standard],
  argon2-cffi, cryptography, python-multipart, and a workspace dependency on `dentalsim`
- [X] T004 Create the root `pyproject.toml` as a uv workspace:
  - members `engine` and `studio/server`
  - shared ruff, mypy and pytest settings: markers `unit`, `integration`, `benchmark`, `regression`, with
    `--strict-markers`
  - add `.python-version` (3.12), then run `uv lock` and `uv sync`, which creates `uv.lock`
- [X] T005 [P] Scaffold `studio/ui/` with Vite, React 19 and TypeScript 5:
  - `package.json`, `tsconfig.json`, `.nvmrc` (24)
  - `vite.config.ts` with a dev proxy from `/api` to `https://localhost:8443`
  - add mantine, @tanstack/react-query, react-router, three, @react-three/fiber, @react-three/drei, openapi-typescript
    (dev), vitest and @playwright/test
  - scripts: `dev`, `build`, `test`, `e2e`, `lint`, `typecheck`, `gen:api`, `gen:conventions`
- [X] T006 [P] Extend `.gitignore` with:
  - `.venv/`, `node_modules/`, `studio/ui/dist/`, `__pycache__/`
  - solver scratch files: `*.frd`, `*.dat`, `*.sta`, `*.cvg`, `spooles.out`
  - `fea-implant-teaching.zip` and any `DentalSimData/`
- [X] T007 [P] Create `engine/src/dentalsim/config.py`, a pydantic settings object:
  - reads `DENTALSIM_DATA` (default `D:\DentalSimData`), `DENTALSIM_CCX`, the repository root (found by walking up to
    `docs/conventions.md`), the home subnets and port 8443
  - `ensure_data_folder()` creates `store/`, `exports/`, `published-media/`, `logs/`, `backups/`, `certs/`, `work/`
    and `estimate/`
- [X] T008 [P] Create `engine/src/dentalsim/version.py`:
  - engine version = package version + `+g<short commit>` from git, for provenance only
  - `engine_source_hash()`: SHA-256 over the sorted paths and contents of the result-affecting engine source
    (`conventions/`, `model/`, `blocks/`, `templates/`, `assembly/`, `geometry/`, `mesh/`, `physics/`,
    `solvers/`, `post/`, `pipeline.py`; research R14). Interface, server and documentation files are excluded,
    so their commits never mark stored runs out of date (Principle VI)
  - `dev_build = True` when `git status --porcelain -- engine/` is not empty (R14)
  - also reports the gmsh version
  - add `engine/tests/unit/test_version.py`: the hash is stable, changes when a file in `post/` changes, and does
    not change when a file in `exports/`, `commands/` or `studio/` changes
- [X] T009 Create `engine/src/dentalsim/cli.py`:
  - argparse with a registry that loads subcommands from `engine/src/dentalsim/commands/`
  - a global `--json` flag
  - exit codes 0, 1 and 2 as in contracts/cli.md: a `Refused` exception maps to 1, any other error to 2
- [X] T010 [P] Create `engine/tests/conftest.py` and `studio/server/tests/conftest.py`: a temporary data-folder
  fixture, skip `benchmark` tests when `DENTALSIM_CCX` is unset, and fixed-ULID and fixed-time helpers

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Plan stage 0:

- the solver and its calibration
- the conventions document
- core models, the store and hashing, and the database
- the physics registry and the core of the literature library, with the seed library approved
- the secured server, the queue and worker, the interface shell, and the merge gate

**⚠️ CRITICAL**: no user story work can begin until this phase is complete.
**Stage gate**: `dentalsim conventions check` passes, `dentalsim solver check` and the solver smoke test pass, and
the seed library is approved.

### Solver (research R3)

- [X] T011 **Owner (main PC)**: get CalculiX ccx 2.23 for Windows, the open-source build with PaStiX
  (revised 2026-11-05: research R3)
  - `ccx_static.exe` from the dhondt.de Windows package; no Intel MKL DLLs (MKL is not open source)
  - put it in `D:\DentalSimTools\calculix\` and set `DENTALSIM_CCX`
  - with Claude, record the download source and steps in `docs/setup/solver.md` (written; follow it)
- [X] T012 [P] Implement `engine/src/dentalsim/solvers/calculix/build.py`:
  - find the binary and run it on a one-element input
  - parse the version string and confirm PaStiX is accepted (SPOOLES as the fallback; PARDISO is never chosen on
    its own, and a program that loads Intel MKL is reported)
  - compute the binary's SHA-256 and return `solver_build_id = "ccx-<version>-<solver>-sha256:<hash>"`
- [X] T013 [P] Implement `engine/src/dentalsim/solvers/calculix/inp_writer.py`, which writes:
  - `*NODE`, `*ELEMENT, TYPE=C3D10`, and `*ELSET` per part and material
  - `*MATERIAL`/`*ELASTIC` and `*SOLID SECTION`
  - `*BOUNDARY` on the support node sets
  - one `*STEP` / `*STATIC, SOLVER=PASTIX` per unit load case, with `*CLOAD` spread over a node patch
  - `*NODE FILE U`, and `*EL PRINT` of S, E, COORD and EVOL to `.dat`
  - `*NODE PRINT` of the reaction forces RF on the support node sets, for the force-balance check (FR-023)
- [X] T014 [P] Implement `engine/src/dentalsim/solvers/calculix/runner.py`:
  - run `ccx` as a child process in a per-job work folder, with `OMP_NUM_THREADS` and `CCX_NPROC_EQUATION_SOLVER`
  - stream stdout for the stage and progress, and capture the solver messages and the relative residual of each
    step (PaStiX prints its refinement error; PARDISO and SPOOLES print none)
  - on cancel or watchdog, end the whole process tree with psutil
  - on failure, raise `SolverFailed` with a plain-language reason
- [X] T015 [P] Implement `engine/src/dentalsim/solvers/calculix/frd_reader.py` (nodal displacements per step) and
  `engine/src/dentalsim/solvers/calculix/dat_reader.py` (integration-point stress, strain and coordinates, plus element
  volumes, per step, plus the support reaction forces), returning NumPy arrays
- [X] T016 Add `dentalsim solver check` in `engine/src/dentalsim/commands/solver.py`, which prints the version, the
  equation solver and SHA-256. Add `engine/tests/benchmark/test_solver_smoke.py`: one C3D10 cube under uniaxial tension must
  give the displacement σL/E within 1e-6 relative.

### Conventions document (FR-050, research R6)

- [X] T017 Draft `docs/conventions.md` (Claude drafts, the owner approves in T022): prose for the owner, plus one fenced
  ```` ```yaml conventions ```` block with:
  - `version: 1.0.0`, units, the frame and the load-angle sign
  - `load_patch_radius_mm`
  - regions: the crestal ring with its sector rule, the implant neck, and whole-component regions for the abutment,
    screw, crown, pontic and connector
  - the strain measure, and the Frost scale: 4 colour-blind-safe categories whose edges are library `parameter_key`s
  - the strength rules table and `mesh_check_limit: 0.05`
  - `equilibrium_tolerance` (proposed 1e-6 relative): the largest allowed force-balance error per load case (FR-023)
  - `max_committed_asset_bytes` (proposed 1 MB): the size above which a published asset goes to published media,
    not git (constitution, Scope)
  - the teaching-only notice text
  - the table mapping each library category to its unit dimension
- [X] T018 Implement `engine/src/dentalsim/conventions/schema.py` (pydantic, `extra="forbid"`) and
  `engine/src/dentalsim/conventions/loader.py`:
  - extract the fenced block and validate it
  - compute the SHA-256 content hash
  - a cached `get_conventions()` raises `ConventionsMissing`, so nothing runs without a valid block
- [X] T019 [P] Implement `engine/src/dentalsim/conventions/regions.py`, region predicates on integration-point
  coordinates and material group (research R5):
  - crestal ring: cortical bone within the distance of the implant or tooth surface set in the conventions, and above
    the depth below the crest set in the conventions
  - implant neck, and whole-component regions
  - surface distance from each block's report anchors (axis and radius profile)
  - mesial, distal, buccal and lingual sectors; the worse side is the sector with the highest mean
- [X] T020 [P] Implement `engine/src/dentalsim/conventions/codegen.py`:
  - writes `studio/ui/src/viewer/colourScales.generated.ts`: Frost categories and colours, the continuous stress scale,
    units and the notice text
  - wire it to `npm run gen:conventions`
- [X] T021 Add `dentalsim conventions check` (prints the version and hash) in
  `engine/src/dentalsim/commands/conventions.py`, plus the conventions regression tests in
  `engine/tests/unit/test_conventions.py` (SC-014):
  - the block parses and has a semver version
  - the frame is right-handed
  - the load-angle sign turns +30° into a force tilted lingually
  - every region named by a registered block type exists
  - the generated TypeScript file is up to date
- [X] T180 **Owner**: review `docs/conventions.md` 1.1.0 (the independent-review revision, proposed 2026-11-06):
  above all the strain measure (ν′ = each material's own ν instead of 0.5) and the 2 mm crown-statistics
  exclusion around each contact point. The integration-point volume rule is decided: det J × Gauss weight
  (owner, 2026-11-06). Record the approval date in its change log.
  - Approved by the owner on 2026-11-06: ν′ = each material's own ν; 2 mm crown-statistics exclusion; 1.1.0 as a
    whole.
- [X] T022 **Owner**: review `docs/conventions.md`. Confirm or change the frame, load-angle sign, regions, strength
  rules and the 5% mesh-check limit, and record the approval date in the document's change log.

### Core models (data-model.md, contracts/scenario-format.md)

- [X] T023 [P] Implement `engine/src/dentalsim/model/settings.py` and `engine/src/dentalsim/model/scenario.py`. All
  models use `extra="forbid"`.
  - settings: `DesignValue`, and `LibraryValue` with `entry_id`, `entry_version`, `choice` (`typical`,
    `source:<id>` or `custom`), `value` and `outside_range`
  - scenario: `Scenario` in format `dentalsim.scenario/1`, `BlockInstance`, `Placement`, and `LoadCase` (pattern
    `single_bite`, `chewing_cycle` or `clenching`, direction, contact points, cycles), plus `detail_level` and
    `brand_comparison`. A `chewing_cycle` holds a list of instants (contact points, direction, force share) and a
    library-sourced cycle count; `clenching` also covers bruxism (FR-008). Force magnitude and cycle count are
    `LibraryValue`s; direction, contact points and shares are `DesignValue`s (FR-052)
- [X] T024 [P] Implement the remaining models (data-model.md):
  - `engine/src/dentalsim/model/validity.py`: `Problem` with the codes `overlap`, `unsupported_pontic`,
    `in_nerve_canal`, `in_sinus`, `in_root`, `exceeds_bone`, `template_misfit` and `design_limit`; `ValidityResult`
  - `engine/src/dentalsim/model/results.py`: `KeyNumber`, `ResultSet`, `LockedResult`
  - `engine/src/dentalsim/model/run.py`: `Run`, with statuses, flags and `is_publishable()`
- [X] T025 Add `engine/tests/unit/test_model_no_patient_fields.py` (FR-048): unknown fields are refused, and no field
  name in any model matches the patient-data deny list (patient, name, birth, mrn, address, scan, photo)

### Store and hashing (research R14)

- [X] T026 [P] Implement `engine/src/dentalsim/store/hashing.py`:
  - canonical JSON: sorted keys and normalised floats
  - `input_record()` and its SHA-256, built from the resolved scenario, library entry versions and content hashes,
    detail level, conventions version, engine source hash (T008, not the commit), solver build id and gmsh version
- [X] T027 [P] Implement `engine/src/dentalsim/store/cas.py`, a content-addressed store under `<data>/store/`:
  zstd-compressed NumPy and JSON blobs keyed by input hash and artefact name, with atomic writes (temp file, then
  rename), and `put`, `get` and `exists`
- [X] T028 Implement `engine/src/dentalsim/store/staleness.py`: compare a stored item's input record with the current
  engine, conventions and library state, and return the flags `out_of_date` (FR-058) and `needs_reverification`
  (FR-051). Stale items are never reused silently.

### Persistence (SQLite, research R12)

- [X] T029 Implement `engine/src/dentalsim/persistence.py`:
  - Protocol interfaces that engine modules depend on: `LibraryRepo`, `CapabilityRepo`, `ScenarioRepo`, `RunRepo`,
    `JobRepo`, `VerificationRepo`, `PublicationRepo`, `EventSink`
  - `open_repos(data_dir)`, which imports `dentalsim_studio.db` lazily so that the command line and the studio share
    one database
- [X] T030 Create `studio/server/src/dentalsim_studio/db/schema.sql` and
  `studio/server/src/dentalsim_studio/db/migrations.py`:
  - migrations by `PRAGMA user_version`, with WAL and foreign keys
  - one table per data-model.md entity: projects, scenarios, owner_templates, runs, previews, studies, jobs, events,
    library_entries, library_versions, sources, version_sources, capability_status, verification_reports,
    mesh_checks, publication_records, published_scenarios, published_versions, exports, promo_clips, settings
  - an FTS5 table, `search_index`
- [X] T031 Implement `studio/server/src/dentalsim_studio/db/connection.py` (WAL, foreign keys, busy timeout, runs the
  migrations on open) and `studio/server/src/dentalsim_studio/db/repos/{jobs,events,capabilities,settings}.py`, with
  round-trip tests in `studio/server/tests/test_db_basics.py`

### Physics capabilities (FR-011a)

- [X] T032 Implement `engine/src/dentalsim/physics/registry.py`:
  - a `Capability` with `id`, `linear`, `required_cases`, `write_solver_input()` and `post()`
  - register `bonded_elastic` (linear). Required cases are not hard-coded: at runtime the registry reads them from
    the case files in `content/verification/cases/` whose `capability` field names it (written later, in T074), so
    this task does not depend on T074. A capability with no required case files can never be ready.
  - register `screw_preload`, `periodontal_ligament`, `contact` and `fatigue`: non-linear, with no cases yet, so they
    can never be ready
  - status comes from `CapabilityRepo`, defaulting to `awaiting_benchmark`
- [X] T033 Implement `engine/src/dentalsim/physics/gate.py`, `check_gate(scenario)`. It reads only the blocks'
  `needs_physics` and the result types' `needs_physics`, and returns:
  - the missing capabilities, which refuse previews and runs, each naming its benchmark
  - `locked_results`

### Literature library core (minimum of Story 2; blocks every story)

- [X] T034 [P] Implement `engine/src/dentalsim/library/models.py`: `Source` and `SourceCheck`, `LiteratureEntry`, and
  `LiteratureEntryVersion` with the statuses `proposed`, `approved`, `rejected` and `superseded` and a content hash
  (data-model.md)
- [X] T035 [P] Implement `engine/src/dentalsim/library/yaml_io.py`, which parses `dentalsim.library/1` (format and
  rules in contracts/library-entry-format.md) and also writes approved snapshots. Import rules:
  - required fields must be present
  - `min ≤ typical ≤ max`, and every source value lies within the range
  - units must match the dimension the conventions give for the category
  - a `parameter_key` that already exists becomes a new version, not a duplicate
  - every import has the status `proposed`
- [X] T036 [P] Implement `engine/src/dentalsim/library/source_check.py`:
  - check DOIs with Crossref `https://api.crossref.org/works/{doi}` and PMIDs with NCBI E-utilities `esummary`, using
    httpx with the generic User-Agent `dentalsim-studio/<version>` and no personal data
  - a source matches when the first author's surname, the year and the normalised title (similarity ≥ 0.90) all agree
  - statuses: `resolved_match`, `resolved_mismatch`, `unresolved` or `not_checked` (offline)
- [X] T037 Implement `engine/src/dentalsim/library/service.py` and
  `studio/server/src/dentalsim_studio/db/repos/library.py`. The service covers:
  - import and source checks
  - `approve`: refused unless every source is `resolved_match` (FR-054)
  - `reject`
  - `edit`: creates a new version and supersedes the old one (FR-016)
  - `resolve(parameter_key, choice)`: approved versions only (FR-014)
- [X] T038 Add `dentalsim library import|check|approve|reject|export` in `engine/src/dentalsim/commands/library.py`.
  `check` prints the entry, its values and each source's check result. Add `engine/tests/unit/test_library_rules.py`,
  with Crossref and NCBI mocked:
  - the import validation rules
  - approval is refused on `resolved_mismatch`, `unresolved` and `not_checked`
  - a proposed version cannot be resolved
  - an edit creates a new version
- [X] T039 **Claude research**: write `content/library/proposals/seed-bonded-elastic.yaml`
  (`dentalsim.library/1`), using PubMed, Crossref and Consensus. Entries:
  - **Bone**: cortical bone E and ν; cancellous bone E for D1–D4 and ν; cortical thickness per density class
  - **Titanium**: grade 4 and grade 5 E, ν and yield strength
  - **CoCr**: E, ν and yield strength
  - **Brittle materials**: zirconia, lithium disilicate, feldspathic porcelain and composite E, ν and flexural
    strength
  - **Cement**: resin cement E and ν
  - **Loads**: molar and premolar single-bite force, clenching force, and chewing cycles per year
  - **Frost windows**: the window edges

  Every value must cite at least one DOI or PMID that resolves. Ranges and typical values come from the cited papers;
  no number may be invented.
- [X] T040 **Owner (main PC)**:
  1. Run `uv run dentalsim library import content/library/proposals/seed-bonded-elastic.yaml`.
  2. Run `dentalsim library check` on every entry, and approve or reject each one.
  3. Run `dentalsim library export` and commit `content/library/snapshots/`.
  - Done 2026-11-06 with revision 2 of the seed file (`seed-bonded-elastic.r2-changes.md`): 41 entries and 50
    sources imported into `D:\DentalSimData`; every source resolved and matched (Crossref or PubMed); the owner
    approved all 41 entries (version 1); snapshot `content/library/snapshots/library-snapshot.yaml`.

### Studio server skeleton (research R10)

- [X] T041 Implement `studio/server/src/dentalsim_studio/app.py`, the FastAPI app factory:
  - registers the routers
  - error handlers that return the error shape in contracts/studio-api.md: `not_signed_in`, `validation_failed`,
    `run_refused`, `estimate_too_large`, `publish_refused`, `not_found`, `conflict`
  - serves `studio/ui/dist` with an SPA fallback
  - loads the conventions at start-up and refuses to start without them
- [X] T042 [P] Implement `studio/server/src/dentalsim_studio/auth/network_guard.py`:
  - a subclass of uvicorn's `H11Protocol`, passed as `Config(http=...)`
  - its `connection_made` closes the transport with no HTTP reply unless the peer is loopback or inside a configured
    home subnet; `connection_lost` is short-circuited for refused connections
  - tests in `studio/server/tests/test_network_guard.py`
- [X] T043 [P] Implement sign-in:
  - `studio/server/src/dentalsim_studio/auth/password.py`: an Argon2id hash in the settings table
  - `studio/server/src/dentalsim_studio/auth/sessions.py`: a random session id in an HttpOnly, Secure,
    SameSite=Strict cookie; a 2-hour idle timeout; at most 5 login attempts a minute
  - `studio/server/src/dentalsim_studio/api/auth.py`: `POST /api/auth/login`, `POST /api/auth/logout`, and the session
    dependency on every other route
  - tests in `studio/server/tests/test_auth.py`
- [X] T044 [P] Implement `studio/server/src/dentalsim_studio/auth/certificate.py`. On first start:
  - generate a local CA and a server certificate for the main PC's host name and home-network IP addresses
    (`cryptography`), stored in `<data>/certs/`
  - print the Windows trust steps (certutil) and the exact `New-NetFirewallRule` command: Private profile,
    LocalSubnet, port 8443
- [X] T045 [P] Implement `studio/server/src/dentalsim_studio/events/bus.py` and
  `studio/server/src/dentalsim_studio/api/events.py`:
  - the worker and the server append events to the `events` table
  - `GET /api/events` streams Server-Sent Events by polling the table every 250 ms, and resumes from `Last-Event-ID`
  - event types: `preview.ready`, `run.progress`, `run.finished`, `study.progress`, `job.failed`,
    `validity.changed`, `capability.changed`
- [X] T046 Implement `studio/server/src/dentalsim_studio/api/system.py`:
  - `GET /api/system`: engine version, `dev_build`, solver build id, conventions version, memory and free disk
    (psutil), and the queue summary
  - `GET /api/conventions`
- [X] T047 Add `dentalsim studio serve [--host] [--port]` and `dentalsim studio set-password` (password prompted with
  getpass) in `engine/src/dentalsim/commands/studio.py`. `serve` delegates to
  `studio/server/src/dentalsim_studio/serve.py`, which:
  - creates the data folder, runs the migrations and makes the certificate
  - refuses to start, telling the owner to run `dentalsim studio set-password`, while no password is set (FR-001)
  - starts uvicorn with TLS and the network guard
  - starts the worker process, and restarts it if it exits

### Queue and worker (research R9)

- [X] T048 Implement `studio/server/src/dentalsim_studio/jobs/queue.py` and `studio/server/src/dentalsim_studio/db/repos/jobs.py`:
  - lanes `heavy` and `preview`, with positions
  - an atomic claim (`BEGIN IMMEDIATE`), heartbeat, cancel and requeue at the front
  - job kinds: `run`, `preview`, `mesh_check`, `verification`, `export`, `promo`, `interactive_data`
- [X] T049 Implement `studio/server/src/dentalsim_studio/worker/main.py` and `dentalsim worker` (in
  `engine/src/dentalsim/commands/studio.py`):
  - one loop per lane: heavy runs one job at a time with `OMP_NUM_THREADS` = cores − 2; preview runs one job at a
    time with 2 threads
  - a registry of handlers by job kind, from `worker/handlers/`
  - start-up recovery: a job left `running` becomes `interrupted` and is requeued at the front (FR-056)
  - heartbeats and events
- [X] T050 [P] Implement two worker guards:
  - `studio/server/src/dentalsim_studio/worker/power.py`: calls `SetThreadExecutionState(ES_CONTINUOUS |
    ES_SYSTEM_REQUIRED)` through ctypes while jobs exist, and releases it when idle
  - `studio/server/src/dentalsim_studio/worker/watchdog.py`: when system memory passes 92%, ends the solver process
    tree and records "failed: out of memory, try a lower detail level"
- [X] T051 Add `studio/server/tests/test_queue_recovery.py`:
  - two heavy jobs never run at once
  - a job left `running` at start-up becomes `interrupted`, then queued at the front
  - queued jobs keep their order

### Interface shell

- [X] T052 [P] Implement the interface's API layer:
  - `studio/ui/src/api/client.ts`: fetch with credentials, mapping the error shape to a typed `StudioError`
  - `studio/ui/src/api/schema.d.ts`, generated from the server's OpenAPI by `npm run gen:api`
  - the TanStack Query provider in `studio/ui/src/main.tsx`
- [X] T053 [P] Implement `studio/ui/src/api/events.ts`: an EventSource hook that reconnects automatically and refreshes
  queries on reconnect, so the laptop shows a run's current status again after losing its connection (edge case)
- [X] T054 Implement `studio/ui/src/App.tsx` and `studio/ui/src/pages/Login.tsx`:
  - a Mantine AppShell with navigation to Builder, Results, Compare, Projects, Queue, Library, Verification,
    Exports, Publish and Physics
  - redirect to sign-in on 401
  - a system bar showing the engine version, a dev-build badge, the conventions version, memory and disk
- [X] T055 [P] Implement `studio/ui/src/components/TeachingNotice.tsx`, using the notice text from the conventions
  (FR-028)

### Merge gate, boundaries, estimates

- [X] T056 Implement `dentalsim check` in `engine/src/dentalsim/commands/check.py`:
  - runs ruff, mypy, and pytest `unit` and `integration`
  - runs `benchmark` and `regression` only when `DENTALSIM_CCX` is set; otherwise the result is "incomplete",
    which exits with code 1 and does **not** count as a pass (constitution, Workflow: the full suite must pass before
    any merge). On the laptop, `dentalsim check --quick` gives fast feedback without the solver, but it is never a
    merge gate
  - runs `npm --prefix studio/ui run typecheck` and `npm --prefix studio/ui test`
  - records only a complete pass, with the engine commit, in the settings table, for merges and the publish
    checklist
  - add `engine/tests/unit/test_check_gate.py`: with `DENTALSIM_CCX` unset, `check` exits 1, reports "incomplete" and
    records no pass
- [X] T057 [P] Add `engine/tests/unit/test_import_boundaries.py` (SC-012): no module outside `dentalsim/blocks/` and
  `dentalsim/templates/` imports a block module or names a block `type_id`, and `studio/server` imports no block module
- [X] T058 Implement `engine/src/dentalsim/estimate/calibrate.py` and `engine/src/dentalsim/estimate/model.py`
  (research R8):
  - mesh a gmsh box at 3 sizes and solve 3 unit cases at each
  - measure wall time, peak memory (polling the ccx process with psutil) and disk use
  - fit, per solver build: time ≈ a·DOF^b, memory ≈ c·DOF^d, disk ≈ e·DOF·cases
  - write `<data>/estimate/coefficients.json`; `refit()` later adds finished runs
- [X] T059 **Owner (main PC)**: run `uv run python -m dentalsim.estimate.calibrate` (it uses the heavy lane's thread
  count, `dentalsim.threads`; about 10 minutes, so choose a time when the PC is free), then record the measured
  numbers in `docs/setup/solver.md`

**Checkpoint**: the foundation is ready. The conventions are approved, the solver passes its smoke test, the seed
library is approved, and the secured server and worker start.

---

## Phase 3: User Story 1, slice 1a: single implant crown end to end (Priority: P1) 🎯 MVP

**Goal**: build a single implant crown from blocks (jaw segment, implant, abutment, crown) or from its template;
preview it; run it in detail; and see Frost-coloured bone strain, % of strength and the movement animation. All of
this is gated by the bonded-elastic textbook benchmarks.

**Independent Test**:

1. Confirm that a preview or run is refused, naming the benchmark, while bonded elastic is "awaiting benchmark".
2. Run `dentalsim verify --physics bonded_elastic`.
3. Build "Single crown (implant)" from its template, change it, run it in detail, and check the results (quickstart
   row 1a).

### Block framework and slice 1a blocks

- [X] T060 [US1] Implement `engine/src/dentalsim/blocks/base.py` and `engine/src/dentalsim/blocks/registry.py`.
  - `BlockType` follows contracts/scenario-format.md:
    - identity: `type_id`, `version`, `slice`
    - a pydantic settings model whose fields carry `group` (one of 5) and `kind` (library with a `parameter_key`, or
      design with limits)
    - `attach_points`, `needs_physics(settings)`, `result_types` (each with its own `needs_physics`) and
      `report_regions`
    - methods: `build_geometry(ctx) -> Parts`, `keep_out(ctx)`, `check(ctx)`, `occlusal_points(ctx)`,
      `report_anchors(ctx)` and `settings_json_schema()`
  - `Parts` carry material tags, named support surfaces and refinement hints
  - the registry discovers block modules automatically
- [X] T061 [P] [US1] Implement `engine/src/dentalsim/blocks/jaw_segment.py`:
  - **Settings**: mandible or maxilla; region given as FDI site codes; an arch curve for any region up to a full
    arch
  - **Bone shape**: the ridge cross-section (height, width) swept along the curve; a cortical shell, its thickness
    from the library; a cancellous core with E and ν by density class D1–D4; the crestal bone level
  - **Keep-out volumes**: the nerve canal (mandible), the sinus floor (maxilla), and neighbouring root envelopes
  - **Attach points and supports**: named sites; the mesial and distal cut faces are the supports
  - **Results**: result type `bone_strain`; report region `crestal_ring`
  - Done 2026-11-06. Teeth are placed along a parabolic arch by mean mesiodistal crown widths per tooth type
    (`TOOTH_WIDTHS_MM`, Wheeler's tables, block geometry for the owner to review with the template in T067). Crestal
    bone loss lowers the bone uniformly over the segment. Mirrored quadrants are built as mirror-image arches, so no
    reflection reaches the mesh.
- [X] T062 [P] [US1] Implement `engine/src/dentalsim/blocks/implant.py`:
  - **Settings**: diameter, length, thread design (an axisymmetric V, buttress or square profile, smeared into a
    cylinder at the preview level), connection type, material from the library, and healing state
    (`immediately_loaded` adds `contact` to `needs_physics`)
  - **Attachment**: attaches to a jaw `site` and offers `platform`
  - **Checks**: `exceeds_bone`, `in_nerve_canal`, `in_sinus`, `in_root`
  - **Result types**: `implant_stress`; `micromotion` (needs `contact`); `implant_fatigue_life` (needs `fatigue`)
  - **Report regions**: `implant_neck`, `implant`
  - Done 2026-11-06. Threads are never smeared by detail level: conventions 1.1.0 ("Detail levels") keeps geometry
    identical at every level, so a plain cylinder is the design choice `thread: none` (the named threads-on-or-off
    sensitivity case). Threaded previews take about 11 s (T070), which the owner accepted on 2026-11-06.
- [X] T063 [P] [US1] Implement `engine/src/dentalsim/blocks/abutment.py`:
  - **Settings**: type (straight or angled), angle, height and collar; retention by screw or cement
  - **Parts**: a screw-access channel, and the retention screw as its own part tagged `screw`
  - **Attachment**: attaches to the implant `platform` and offers `crown_seat`
  - **Result types**: `abutment_stress`; `screw_stress` (needs `screw_preload`); `screw_fatigue_life` (needs
    `fatigue` and `screw_preload`)
- [X] T064 [P] [US1] Implement `engine/src/dentalsim/blocks/crown.py`:
  - **Settings**: crown height, mesiodistal and buccolingual widths, material from the library
  - **Shape**: cusp geometry with the named occlusal points `buccal_cusp`, `lingual_cusp`, `central_fossa`,
    `mesial_marginal_ridge` and `distal_marginal_ridge`
  - **Attachment and results**: attaches to `crown_seat`; result type `crown_stress`; offers `connector` sockets for
    slice 1b
- [X] T065 [US1] Add the block unit tests `engine/tests/unit/blocks/test_jaw_segment.py`, `test_implant.py`,
  `test_abutment.py` and `test_crown.py`: volume, bounding box, attach positions, keep-out volumes, and every validity
  rule (scenario-format contract)

### Assembly, templates, geometry, mesh

- [X] T066 [US1] Implement the assembly:
  - `engine/src/dentalsim/assembly/placement.py`: turns attach chains into transforms in the conventions frame
    (origin at the crest of the first site, +z along the reference implant axis) and applies the placement offsets
    and axis angles
  - `engine/src/dentalsim/assembly/validity.py`:
    - checks design limits and keep-out intrusions
    - finds pairwise overlaps by OCC boolean intersection volume above a tolerance, located at the intersection
      centroid
    - adds each block's `check()` results
    - returns a `ValidityResult` (FR-011)
    - has a fast mode (≤ 1 s) for checks while building
  - Done 2026-11-06. Placement offsets are given in the frame of the socket a block attaches to (for blocks on
    the jaw, the frame's own axes), so an abutment's rotation turns its tilt about the implant axis; the root is
    not moved, and a tilted reference implant turns the whole assembly so that its axis is +z. The fast mode
    (about 0.01 s) runs the design limits and the blocks' own checks; the full mode (about 1 s for slice 1a) adds
    the OpenCASCADE keep-out and overlap checks. Overlaps settled by a chain of `displaces` (a screw inside the
    implant inside the bone) are not collisions.
- [X] T067 [US1] Implement templates:
  - `engine/src/dentalsim/templates/loader.py`: loads built-in YAML and owner templates; checks `fit_rules` against
    the jaw segment, returning `template_misfit` problems that say what must change; resolves each library
    `parameter_key` to its typical approved version
  - `engine/src/dentalsim/templates/single_crown_implant.yaml`: Claude proposes the design defaults, and the owner
    approves them
  - Done 2026-11-06; the owner approved the proposed defaults the same day (`approval.status: approved`,
    `approved_on: 2026-11-06`), so the builder no longer marks the template "defaults awaiting approval". Fit
    rules: one-tooth region, a first or second molar site, a crown at most 0.5 mm wider than the site tooth's mean
    width, and a 2 mm safety distance from the implant apex to the nerve canal (or the sinus floor or ridge base).
    Two load cases: an axial bite on the central fossa and a 30° oblique bite on the buccal cusp, both
    `load.bite_force.molar`.
- [X] T068 [US1] Add `engine/tests/unit/test_assembly_validity.py` (Acceptance 3):
  - an implant moved into the nerve canal gives `in_nerve_canal` with its location
  - overlapping parts give `overlap`
  - an implant longer than the bone gives `exceeds_bone`
  - a template on an unsuitable segment gives `template_misfit`
  - the template as delivered is valid
- [X] T069 [US1] Implement `engine/src/dentalsim/geometry/assemble.py`:
  - build every block's parts in gmsh OCC and apply the transforms
  - join them with `BooleanFragments`, so touching parts share nodes (bonded)
  - add physical groups per part and material, plus named support and occlusal-patch surfaces
  - Done 2026-11-06. Each displacing part cuts with its whole original solid, in an order where a part acts as a
    tool before it is cut, so chains settle (screw in implant in bone); cutters act after that. Physical groups:
    one volume group per part (`<instance>.<part>`) and one surface group per support; the occlusal patches are
    chosen on the mesh (conventions), from the outer faces of the loaded block. The single crown joins in
    about 2.5 s.
- [X] T070 [US1] Implement meshing:
  - `engine/src/dentalsim/mesh/levels.py`: the detail levels `preview`, `standard`, `fine` and `extra_fine` as
    Distance and Threshold size fields, using the blocks' refinement hints at threads, the crest and connectors
  - `engine/src/dentalsim/mesh/build.py`:
    - C3D10 elements, meshed single-threaded with a fixed seed
    - a quality check (minimum SICN) that fails with a plain reason
    - the load patch from `engine/src/dentalsim/mesh/load_patch.py` (written with conventions 1.1.0: faces by
      centroid distance, uniform traction, consistent nodal weights)
    - a connectivity check (FR-023): every part must be joined to a supported part through shared faces, not only
      nodes or edges; otherwise the run fails before the solve, naming the part ("the crown does not touch the
      abutment")
    - extraction of nodes, elements, element sets, support node sets and per-part outer-surface triangles
  - `engine/src/dentalsim/mesh/predict.py`: predicts the DOF from the size fields before meshing
  - the `preview` level targets about 15–25 k DOF (research R7: 48 k DOF already takes 8 s on 2 threads)
  - Done 2026-11-06. Measured on the single-crown template (threaded implant): preview 59 k DOF, standard
    209 k, fine 382 k; geometry plus mesh take 5.4 s at preview (R7 budget 4 s). The preview cannot reach
    15–25 k DOF while the geometry is fixed across levels (threads, a 0.4 mm implant wall round the conical
    connection). The owner accepted a preview of about 11 s on 2026-11-06 (SC-002 and FR-017 amended, research
    R7). Edge nodes follow the curved geometry; elements whose
    Jacobian would fall too low are made straight. Netgen optimisation keeps the minimum SICN at or above 0.027
    (the floor is 0.01). The DOF prediction is a power law of ∫ 1/h³ dV fitted on the template (within 14%).

### Bonded-elastic benchmarks: the gate (FR-011a, research R15)

- [X] T071 [P] [US1] Implement verification cases and the case runner:
  - `engine/src/dentalsim/verification/cases.py`: the VerificationCase YAML schema from data-model.md
  - `engine/src/dentalsim/verification/runner.py`:
    - solves each case at the standard and preview levels
    - compares absolute, relative or ratio quantities with their tolerances
    - stores the case file's SHA-256, and refuses a case file with uncommitted git changes
    - writes a VerificationReport, including `preview_accuracy` with the set of cases it was measured on
      (`textbook` now; `published` once US5's cases exist, which then takes precedence, FR-017)
    - sets the capability status: ready only when every required case passes in one report; any failure locks it
      again (FR-062)
    - a capability stays ready only while its report covers every current required case: adding a required case
      file returns it to "awaiting benchmark" until a new report passes (extend `physics/registry.capability_state`
      to compare the report's case ids with the current required cases)
    - emits `capability.changed`
  - Done 2026-11-06. The required cases a passing report covered are stored with the capability status (studio
    database schema version 2), and `summarise` keeps a capability ready only while they include every current
    required case. The preview accuracy compares each case's preview values with its standard values.
- [X] T072 [P] [US1] Implement `engine/src/dentalsim/verification/builders/textbook.py` with these cases:
  - cantilever beam: Timoshenko tip deflection
  - thick-walled cylinder under internal pressure: Lamé radial and hoop stress
  - NAFEMS LE10: σyy at point D = −5.38 MPa
  - constant-strain patch test
  - two bonded bars in series
  - cylinder mesh-convergence series

  Also add pressure loads (`*DLOAD`) to `engine/src/dentalsim/solvers/calculix/inp_writer.py`.
  Done 2026-11-06. Textbook meshes are straight-sided 10-node tetrahedra, so CalculiX and scikit-fem solve the
  same discrete problem; point values (LE10's σyy at D) are integration-point values extrapolated to the node and
  averaged. Prescribed displacements (`*BOUNDARY` with a value) were added for the patch test.
- [X] T073 [P] [US1] Implement `engine/src/dentalsim/solvers/skfem/crosscheck.py`, which solves the textbook meshes
  with scikit-fem (quadratic tetrahedra, linear elasticity) as an independent second solver and reports agreement
  Done 2026-11-06. Stress and strain are recovered from scikit-fem's displacements at CalculiX's integration
  points; a quantity agrees when the two differ by under a tenth of the case's tolerance band.
- [X] T074 [US1] **Claude drafts, owner approves**: write the textbook case files below, each with its quantity,
  expected value and source, tolerance, and `capability: bonded_elastic` (read by the registry, T032). Each case's
  inputs are the ones its textbook source states, cited in the file (FR-035). Commit them in their own commit before
  any case is run.
  Approved by the owner and committed on its own (c221312) on 2026-11-06, then run on the main PC with
  `dentalsim verify --physics bonded_elastic` (report 01M6R0C94DNZFB3VQR896V59HH, 2 min 12 s): all six pass on
  ccx 2.23 PaStiX and agree with scikit-fem, and `bonded_elastic` is ready. Standard level: cantilever 0.152115
  mm (−0.36%), Lamé u_r −0.15%, hoop −0.21%, radial −0.45%, axial −0.12%, LE10 σyy at D −5.401 MPa (+0.40%),
  bonded bars exact, patch test 1.2e-7, convergence 0.04% on the finest mesh with errors falling. Previews differ
  from detailed results by up to 3% (LE10: −5.567 against −5.401 MPa). The bonded-bars case uses the same ν/E in
  both bars (200 GPa with ν 0.30, 100 GPa with ν 0.15), so the field is exactly uniaxial.
  - `content/verification/cases/textbook-cantilever.yaml`
  - `textbook-lame-cylinder.yaml`
  - `textbook-nafems-le10.yaml`
  - `textbook-patch-test.yaml`
  - `textbook-bonded-bars.yaml`
  - `textbook-cylinder-convergence.yaml`
- [X] T075 [US1] Add `dentalsim verify [--case ID ...] [--physics ID]` in
  `engine/src/dentalsim/commands/verify.py`, and `engine/tests/benchmark/test_textbook_bonded_elastic.py`: every
  textbook case passes on CalculiX and agrees with the scikit-fem cross-check. Also add
  `engine/tests/benchmark/test_unsupported_models_refused.py`: a cube held only along one edge, and an assembly with
  a part that touches nothing, are refused by the convergence or connectivity checks and never reported
  Done 2026-11-06; the textbook test passes since the case files were committed (T074), in 2 min on the main PC.

### Solve pipeline and results (research R4, R5)

- [X] T076 [P] [US1] Implement `engine/src/dentalsim/post/fields.py`: von Mises stress, maximum principal stress, von
  Mises equivalent strain in µε, and principal strains at the integration points
- [X] T077 [P] [US1] Implement `engine/src/dentalsim/post/superpose.py`:
  - combines the unit cases (1 N along x, y and z per contact point) into each LoadCase: force × direction (angle and
    azimuth, with the conventions sign) × share
  - a `chewing_cycle` is combined instant by instant, and its key numbers are those of the worst instant, which is
    named (FR-008)
  - acts on the displacement, stress and strain tensors before invariants are computed
  - refuses if any capability in use is non-linear
  - Done 2026-11-06. Runs solve 3 unit cases per contact point; previews solve one unit case per load-case
    instant (1 N in its direction over its contact points), so a preview needs one solve per active instant. The
    integration test checks that both give the same key numbers to 1e-4.
- [X] T078 [P] [US1] Complete `engine/src/dentalsim/post/statistics.py` (the weighted mean and the conventions
  percentile method were written with conventions 1.1.0; add the crown and pontic exclusion within
  `crown_stats_exclude_radius_mm` of each contact point): the volume-weighted mean and 95th
  percentile over integration points (weight = det J × Gauss weight, from `post/integration.py`; conventions
  1.1.0). Nodal peaks are never reported.
- [X] T079 [US1] Implement `engine/src/dentalsim/post/key_numbers.py`:
  - **Crestal strain** per implant on the worse side, with the sector named and its Frost window
  - **Component stress** as % of the strength reference, by the conventions rules (metals: von Mises against yield;
    brittle materials: maximum principal against flexural strength), as mean and p95, with the library entry and
    version
  - **Displacement** values
  - **Locked results**, for example the screw shown as "not reported: needs screw preload"
- [X] T080 [P] [US1] Implement `engine/src/dentalsim/post/gltf.py`, which writes one `.glb` per part and load case:
  - the outer surface, with the vertex attributes `_STRAIN_UE`, `_STRESS_VM`, `_PCT_STRENGTH` and `_FROST_CLASS`
  - the displacement as a morph target
  - surface colours are for display only; key numbers come from integration points
- [X] T081 [US1] Implement `engine/src/dentalsim/pipeline.py`:
  - the stages: resolve, validate, gate, geometry, mesh, solver input (unit cases spread over the occlusal patch;
    previews use only the active contact points), solve, read, superpose, key numbers, glTF, and storage in the
    content-addressed store under the input hash
  - a progress callback reporting the stage, percent and time remaining
  - a failure stores nothing partial (FR-022)
  - the convergence checks per unit case after the solve (FR-023; research R3), each failing the run with its
    reason:
    - with `engine/src/dentalsim/post/convergence.py` (written with conventions 1.1.0)
    - force balance: the solver's total support reaction against the applied load, per component, within
      `equilibrium_tolerance`
    - moment balance: the nodal support reactions' moment against the patch forces' moment, within
      `moment_tolerance`
    - energy: the work of the loads (Σ fᵢ · uᵢ over the patch nodes) against twice the strain energy (from the
      integration-point stress, strain and volumes), within `energy_tolerance`
    - the solver residual of the step (`SolverRunResult.step_residuals`), within `residual_tolerance`, where the
      solver reports one
  - returns the convergence (force balance, energy balance and residual per case, solver messages), the actual DOF,
    time, memory and disk, `locked_results`, and every version
- [X] T082 [US1] Implement `engine/src/dentalsim/estimate/predict.py` (FR-009, FR-057):
  - from the predicted DOF and the coefficients, returns `{dof, time_s, memory_gb, disk_gb, fits, warnings}`
  - refuses when the memory needed is above 85% of the solving machine's physical memory, suggesting a lower
    detail level (and, when one is configured, the rented solver machine, FR-064)
  - warns when free disk is below twice the expected use
- [X] T083 [US1] Add `dentalsim validate|estimate|run|preview <scenario.json>` in
  `engine/src/dentalsim/commands/scenario.py`, with `--detail`, `--out`, `--id` and `--json`; runs are stored through
  `RunRepo`. Add `engine/tests/integration/test_pipeline_single_crown.py` (marker `benchmark`): build from the template
  and solve a preview; key numbers are present with statistic labels, the screw is locked, and the force balance is
  recorded within tolerance. Solving the same scenario a second time, without the cache, gives key numbers within
  0.1% (SC-007).

### Studio API (US1 scope)

- [X] T084 [US1] Implement `studio/server/src/dentalsim_studio/db/repos/scenarios.py` (projects at their minimum,
  scenarios with `revision`, owner templates) and `studio/server/src/dentalsim_studio/db/repos/runs.py` (runs,
  previews)
- [X] T085 [US1] Implement `studio/server/src/dentalsim_studio/api/catalogue.py`:
  - `GET /api/physics`
  - `GET /api/blocks`: the settings JSON schema with group, kind and limits; attach points; `needs_physics`; result
    types; ready or awaiting status
  - `GET` and `POST /api/templates`
- [X] T086 [US1] Implement `studio/server/src/dentalsim_studio/api/scenarios.py`:
  - `GET` and `POST /api/projects` (minimum)
  - `POST /api/scenarios` from a template, returning `template_misfit` problems
  - `GET` and `PUT /api/scenarios/{id}`:
    - a save must carry the current revision, otherwise 409 `conflict`
    - library values must be approved versions, otherwise 422
    - `outside_range` is computed for each library value
    - block-version upgrades are offered when a newer block version exists
  - `POST /api/scenarios/{id}/validate`, which emits `validity.changed`
  - new route `POST /api/scenarios/{id}/geometry`: a tessellated `.glb` of block surfaces and keep-out volumes for the
    builder. Add this route to `contracts/studio-api.md`.
- [X] T087 [US1] Implement `studio/server/src/dentalsim_studio/api/runs.py`:
  - `POST /api/scenarios/{id}/preview`: the gate refuses with `run_refused`, naming the benchmark; cached by input
    hash; preview lane
  - `GET /api/previews/{id}`: `label: "preview"`, with the `accuracy_note` from the latest report's
    `preview_accuracy`
  - `POST /api/scenarios/{id}/estimate`
  - `POST /api/scenarios/{id}/runs`: refused for validity, gate or estimate problems; sets the flags
    `outside_published_range` and `dev_build`
  - `GET /api/runs/{id}`: staleness flags are computed each time it is opened (FR-058)
  - `POST /api/runs/{id}/cancel`, `GET /api/runs/{id}/results`, `GET /api/runs/{id}/assets/{name}.glb`,
    `GET /api/queue`
- [X] T088 [US1] Implement `studio/server/src/dentalsim_studio/worker/handlers/run.py` and
  `studio/server/src/dentalsim_studio/worker/handlers/preview.py`:
  - call `dentalsim.pipeline`
  - emit `run.progress {run_id, percent, stage, remaining_s}`, then `run.finished` or `preview.ready`
  - record failure reasons, honour cancel, and refit the estimates after each run
- [X] T089 [US1] Add `studio/server/tests/test_api_run_refusals.py`:
  - a preview is refused while `bonded_elastic` is awaiting its benchmark, and the refusal names it
  - a run is refused for `in_nerve_canal`, with its location
  - an unapproved library version gives 422
  - an estimate that is too large gives 409 `estimate_too_large`
  - `screw_stress` appears in `locked_results`
  Done 2026-11-06, with the catalogue, scenario and handler tests (`test_worker_solve_handlers.py`); the
  studio tests seed a database from the approved library snapshot (`dentalsim.testing.seed_library`).

### Interface (US1 scope)

- [X] T090 [P] [US1] Implement the viewer:
  - `studio/ui/src/viewer/Scene.tsx` and `studio/ui/src/viewer/GlbModel.tsx`: custom attributes become vertex
    colours, and the morph-target weight is the magnification
  - `studio/ui/src/viewer/colourScale.ts`, built on `colourScales.generated.ts`
  - `studio/ui/src/viewer/Legend.tsx`: the Frost legend, with its source and the "teaching reference" label
- [X] T091 [P] [US1] Implement the settings forms:
  - `studio/ui/src/components/SettingsForm.tsx`: renders a block's settings schema in its five groups, with design
    limits enforced
  - `studio/ui/src/components/LibraryValueField.tsx` (FR-015, FR-052):
    - shows the approved range, the typical value (preselected), and every source with its value
    - offers the choices typical, a source's value, or custom
    - shows the "outside published range" mark, which blocks publishing
- [X] T092 [P] [US1] Implement `studio/ui/src/components/LoadEditor.tsx` (FR-008):
  - pattern: single bite, chewing cycle or clenching
  - force from the library, with angle and azimuth (the conventions sign shown)
  - contact points picked from the blocks' named occlusal points, with shares
- [X] T093 [US1] Implement `studio/ui/src/pages/Builder.tsx`:
  - new scenario from a template, showing the misfit message when it does not fit
  - a block palette from `GET /api/blocks` with ready or awaiting badges, and a block tree
  - add, attach and remove blocks
  - placement controls (position, mesiodistal and buccolingual angles, rotation) and dragging in the 3D scene
  - validate on every move: highlight collisions, and list the problems with location markers
  - save, with revision-conflict handling; save as an owner template; block-version upgrade prompt
  - no block-specific code (SC-012)
- [X] T094 [US1] Implement `studio/ui/src/components/PreviewPanel.tsx`:
  - automatic preview 600 ms after the last change
  - the "preview" label and the accuracy note
  - a refusal that names the missing benchmark
- [X] T095 [P] [US1] Implement `studio/ui/src/components/EstimateDialog.tsx` (detail level, expected time, memory and
  disk, warnings, refusal) and `studio/ui/src/pages/Queue.tsx` (both lanes, progress, time remaining, cancel)
- [X] T096 [US1] Implement `studio/ui/src/pages/Results.tsx`:
  - the Frost-coloured strain map, with legend and source
  - the crestal value per implant, naming the worse side
  - a component stress table: % of the strength reference, mean and p95, source, "teaching reference"
  - displacement values
  - the movement animation, with a magnification slider and the magnification stated
  - a switch from the von Mises equivalent strain gauge to |maximum principal strain| (conventions 1.1.0)
  - retention-screw stress, once reported, labelled "excludes screw preload" (conventions 1.1.0, "Analysis
    assumptions")
  - the locked results, for example "not reported: needs screw preload"
  - the run record: versions, convergence and flags
  - the TeachingNotice on every panel
  Done 2026-11-06 for T090–T097, checked by type checks, lint, unit tests (`tests/slice1a.test.ts`) and the
  production build, not yet in a browser against the running studio (T098). The builder saves each change as a
  new revision (after 0.5 s) and validates it: the fast checks at once, the full ones after a 1.5 s pause; the
  server fills in a new block's library values and takes the new entry's typical value when a design value
  changes a parameter key (another material, say). Dragging moves the selected block along its socket's axes.
  The result surfaces also carry |maximum principal strain| for the strain-gauge switch. Read routes for the
  library entries (`GET /api/library/entries[/{id}]`, needed by the library-value field) and for verification
  cases and reports were added ahead of T108 and T127.
- [X] T097 [P] [US1] Implement `studio/ui/src/pages/Physics.tsx`: each capability with its status, required cases and a
  link to its report

### Slice 1a acceptance

- [X] T098 [US1] **Owner (main PC)**: check slice 1a and record the timings in `docs/setup/performance.md`.
  1. Before verification, confirm that previews and runs are refused, naming `bonded_elastic` (Acceptance 7).
  2. Run `uv run dentalsim verify --physics bonded_elastic`, which must show it "ready".
  3. Work through quickstart row 1a:
     - the nerve-canal collision shows at once, and a run is refused with the location
     - the preview updates within about 13 s, with its accuracy note (SC-002, amended 2026-11-06)
     - the estimate is shown before the run starts
     - a standard run finishes within 30 min (SC-003, provisional: `standard` counts as detailed until the mesh
       check in T136 confirms it, FR-018)
     - every result view is complete, with the screw shown as "not reported"
  Accepted by the owner on 2026-11-06. Claude ran the checks from the command line (same engine functions as
  the studio) on the main PC, in project "Slice 1a acceptance (T098)", and the owner then in the browser;
  timings in `docs/setup/performance.md`:
  1. passed: with the physics status removed (a copy of the database), preview and run are refused, naming
     bonded elastic
  2. passed (T074): report 01M6R0C94DNZFB3VQR896V59HH, bonded elastic ready
  3. collision found by the fast checks in 0.15 s with its location, and the run refused naming it ("enters the
     nerve canal from 9.2 to 10.0 mm below its platform"); the preview carries the accuracy note (up to 3%);
     estimate shown first; standard run 01M6R1MGEDH7WDMZSF5NEC4RZ1 in 2 min 35 s, with every key number and the
     screw "not reported: needs screw preload". The preview took 16–18 s: Windows power throttling put the
     mesher on an efficiency core. Fixed (`dentalsim/qos.py`), it takes 11.8–13.3 s with the data folder on the
     hard disk D: and 10.3–11.5 s on an SSD.
  Found and fixed in the owner's browser test: two gmsh sessions at once in the server (scene and full validity
  check) both failed, now serialised by a process-wide lock in `geometry/occ.py`; the builder hid a failed scene
  (now shown with "Try again"); the builder view fitted the drag gizmo's invisible 100 m planes and showed no
  model (now fitted to the parts only; the root block gets no gizmo); "Add a block" offered a socket that was
  already occupied (a second implant on the one implant site): each socket now has a capacity (one), the
  builder offers only sockets with room, and a block beyond it is named at once by the fast checks and left
  unplaced; a finished run vanished from the queue: the Queue page now lists the 10 latest finished jobs with
  their outcome and a link to the results. In the studio the owner's preview took 13.1 s and the standard run
  1 min 37 s. The owner kept the data folder on the hard disk D: and accepted a preview of about 13 s (SC-002,
  FR-017 and research R7 amended). The owner asked for more realistic crown and jaw-bone shapes in a later,
  separate spec.

**Checkpoint**: MVP. The owner can build, preview, run and inspect a single implant crown.

---

## Phase 4: User Story 1, slice 1b: multi-implant fixed prostheses (Priority: P1)

**Goal**: add pontic, splinting connector and cantilever extension blocks, plus the splinted crowns, implant bridge,
cantilever bridge and full-arch fixed templates. No changes outside `blocks/` and `templates/`.

**Independent Test**: build a cantilever bridge on two implants, delete one implant (the unsupported part is
reported), restore it, save, run in detail and check the results (quickstart row 1b).

- [ ] T099 [P] [US1] Implement `engine/src/dentalsim/blocks/pontic.py`:
  - **Settings**: width, height and occlusal shape; material from the library; named occlusal points
  - **Attachment**: attaches through `connector` sockets
  - **Results**: result type `pontic_stress`
  - **Check**: `unsupported_pontic` unless the pontic is connected on both sides to a supported unit
- [ ] T100 [P] [US1] Implement `engine/src/dentalsim/blocks/splinting_connector.py`: connector cross-section (height,
  width); joins two neighbouring units; result `connector_stress`; region `connector`
- [ ] T101 [P] [US1] Implement `engine/src/dentalsim/blocks/cantilever_extension.py`: cantilever length, unit size and
  material; attaches by a connector to the mesial or distal end of a supported unit; a design limit on the maximum
  length; result `cantilever_stress`
- [ ] T102 [P] [US1] Add the block unit tests `engine/tests/unit/blocks/test_pontic.py`,
  `test_splinting_connector.py` and `test_cantilever_extension.py`: geometry, attach positions and validity rules
- [ ] T103 [US1] Extend `engine/src/dentalsim/blocks/jaw_segment.py` and `engine/src/dentalsim/assembly/placement.py`
  for several sites along the arch (a site list, and spacing from the tooth widths), and confirm that connectors get
  refinement hints in `engine/src/dentalsim/mesh/levels.py`
- [ ] T104 [US1] Add the templates `engine/src/dentalsim/templates/splinted_crowns.yaml`, `bridge_implant.yaml`,
  `cantilever_bridge.yaml` and `full_arch_fixed.yaml`, each with fit rules (minimum segment length, region, number of
  sites)
- [ ] T105 [US1] Add `engine/tests/integration/test_templates_1b.py`:
  - each template validates on a suitable segment and reports `template_misfit` on an unsuitable one
  - deleting an implant from the cantilever bridge reports the unsupported part
  - each template's preview solves (marker `benchmark`)
- [ ] T106 [US1] Check SC-012 for slice 1b:
  - `engine/tests/unit/test_import_boundaries.py` passes
  - the Builder and Results show the new blocks with no interface change
  - fix any leak in generic code itself, not by adding block-specific code
- [ ] T107 [US1] **Owner (main PC)**: work through quickstart row 1b. Then run a 3-implant scenario in detail, which
  must finish within 10 h (SC-013), and record the time in `docs/setup/performance.md`.

**Checkpoint**: every implant-supported fixed scenario in the year-one plan can be built and run.

---

## Phase 5: User Story 2: build and approve the literature library (Priority: P2)

**Goal**: the full library in the studio:

- browse, bulk import, add by hand, check sources, approve or reject
- edits create new versions
- rejecting an entry flags the runs that used it

**Independent Test**: add one proposed entry and one owner entry. Approve one and reject the other. Only the approved
one can be chosen, and both show their sources (quickstart row 2).

- [ ] T108 [US2] Implement `studio/server/src/dentalsim_studio/api/library.py`, calling
  `dentalsim.library.service`, with every library route in contracts/studio-api.md:
  - list (status, category and text filters) and get (with versions and sources)
  - owner add, which creates a `proposed` entry
  - multipart YAML import
  - check-sources, approve and reject `{reason}`
  - `PUT`, which creates a new version
  - export a snapshot
- [ ] T109 [US2] Implement rejection flagging in `engine/src/dentalsim/library/service.py` and
  `studio/server/src/dentalsim_studio/db/repos/runs.py` (FR-055):
  - rejecting a used version sets `uses_rejected_entry` on every run that used it
  - superseded versions stay readable
  - `dentalsim library reject` lists the flagged runs
- [ ] T110 [P] [US2] Implement `studio/ui/src/pages/Library.tsx`:
  - a table filtered by status, category and text
  - entry detail: every version; the range, typical value and values by source; each source's check result
    (authors, year, title similarity)
  - **Check sources**, **Approve** (disabled, with the reason, until every source matches) and **Reject with
    reason** buttons
  - bulk YAML upload, and an edit dialog that creates a new version
- [ ] T111 [P] [US2] Implement `studio/ui/src/components/AddEntryForm.tsx` (FR-053): the owner enters a paper and its
  values (parameter key, category, units, typical, range, values by source, and source fields with a DOI or PMID); the
  entry is saved as `proposed`
- [ ] T112 [US2] Show the library versions used on the run record in `studio/ui/src/pages/Results.tsx` (entry, version,
  value, sources), with a "newer version exists" note (Acceptance 3)
- [ ] T113 [US2] Add `studio/server/tests/test_api_library.py`, with Crossref and NCBI mocked:
  - a proposed entry cannot be chosen (422)
  - approval is refused on a mismatching DOI
  - editing an approved entry creates v2, and an old run still shows v1
  - rejecting a used version flags its runs, which are then not publishable

**Checkpoint**: the library is complete in the studio; every input traces to an approved version (SC-005).

---

## Phase 6: User Story 3: organise and compare runs (Priority: P3)

**Goal**: projects with notes and tags; search; duplicate and change; side-by-side comparison with shared scales;
reopen without re-running; backup and restore.

**Independent Test**: create a project, duplicate a scenario changing one setting, run both, compare them, and find
them again by tag and by note text (quickstart row 3).

- [ ] T114 [US3] Extend `studio/server/src/dentalsim_studio/db/repos/scenarios.py` and add
  `studio/server/src/dentalsim_studio/db/search.py`:
  - notes and tags on projects, scenarios and runs
  - FTS5 index triggers over names, notes and tags
  - setting-value search with `json_each` over the scenario JSON, written `path:op:value` with the operators =, <,
    >, <= and >=
- [ ] T115 [P] [US3] Implement `engine/src/dentalsim/model/diff.py`: a generic settings diff between two scenarios
  (blocks matched by `instance_id`, setting paths, loads, detail level), with no block-specific code
- [ ] T116 [P] [US3] Implement `engine/src/dentalsim/post/compare.py` for two or more result sets:
  - one shared colour scale per field: the union of the ranges, with the Frost windows fixed
  - one shared deformation scale
  - key-number differences, absolute and relative, matched by id
- [ ] T117 [US3] Implement `studio/server/src/dentalsim_studio/api/projects.py`, `api/search.py` and `api/compare.py`,
  and extend `api/scenarios.py`:
  - `GET`, `PATCH` and `DELETE /api/projects/{id}`
  - `POST /api/scenarios` with `{scenario_id}`: duplicate, with `derived_from`
  - `GET /api/scenarios/{id}/diff/{other_id}` and `GET /api/search`
  - `POST /api/compare`: stored results only, under 1 minute (SC-008)
- [ ] T118 [P] [US3] Implement `studio/ui/src/pages/Projects.tsx`: the project list and detail (scenarios, runs,
  studies, notes and tag editing), and a search box (text, tag, setting value) with results grouped by kind
- [ ] T119 [P] [US3] Add duplicate-and-change to `studio/ui/src/pages/Builder.tsx`: the link to the original is shown,
  and the changed settings are highlighted using the diff endpoint
- [ ] T120 [US3] Implement `studio/ui/src/pages/Compare.tsx`:
  - side-by-side viewers that share the camera, colour scale and deformation scale
  - the differing settings, and a table of key-number differences
  - the TeachingNotice
- [ ] T121 [US3] Implement backup and restore (FR-034):
  - `engine/src/dentalsim/backup.py`: one archive with a SHA-256 manifest, holding a SQLite online-backup snapshot,
    the store, published media and a library export; restore checks every hash before replacing anything
  - `dentalsim backup [--to]` and `restore` in `engine/src/dentalsim/commands/backup.py`
  - `POST /api/system/backup` and `/restore` jobs in `studio/server/src/dentalsim_studio/api/system.py`
  - `engine/tests/integration/test_backup_restore.py`
- [ ] T122 [US3] Add `studio/server/tests/test_api_compare_search.py`:
  - a duplicate keeps its link, and the diff lists the one changed setting
  - compare returns shared scales and the differences
  - search by tag, by note text and by `implant-1.length:=:8` finds the scenario
  - reopening a run returns its results without creating a job

**Checkpoint**: comparisons for teaching work; data is backed up.

---

## Phase 7: User Story 4: queue overnight studies and sensitivity checks (Priority: P4)

**Goal**: parameter sweeps and min, typical and max sensitivity runs that execute unattended one at a time, survive
restarts, and end with a summary of the spread.

**Independent Test**: queue a study of at least 6 runs and one sensitivity check, close the browser, restart the main
PC midway, and confirm the next day that everything finished or the failures are explained, and that the summary
appears (quickstart row 4).

- [ ] T123 [P] [US4] Implement `engine/src/dentalsim/study.py`:
  - **Format**: `dentalsim.study/1`, with a base scenario and either `kind: sweep` (axes as `{setting path, values}`)
    or `kind: sensitivity` (a library setting path)
  - **Sweep**: expands into the Cartesian product of resolved snapshots
  - **Sensitivity**: makes the minimum, typical and maximum of the approved range
  - **Load-only axes** (bite angle, force) reuse one solve by superposition (research R4)
  - **Summary**: the spread (min, max, range, % change) of each key number per axis
- [ ] T124 [US4] Implement `studio/server/src/dentalsim_studio/db/repos/studies.py` and
  `studio/server/src/dentalsim_studio/api/studies.py`:
  - `POST /api/studies` (sweep or sensitivity), `GET /api/studies/{id}`, and `POST /api/studies/{id}/cancel`
  - before queuing, a memory and disk check for the whole study (FR-057)
  - statuses `finished` and `finished_with_failures`, and `study.progress` events
- [ ] T125 [US4] Add `dentalsim study <study.yaml>` in `engine/src/dentalsim/commands/study.py`
- [ ] T126 [P] [US4] Implement the study interface:
  - `studio/ui/src/components/StudyDialog.tsx`: choose the settings and values to vary
  - a sensitivity button on each library setting in `studio/ui/src/components/LibraryValueField.tsx`
  - `studio/ui/src/pages/Study.tsx`: progress, per-run status with failure reasons, and the spread summary table
- [ ] T127 [US4] Add `studio/server/tests/test_studies.py`:
  - a 6-run sweep queues in order, one heavy run at a time
  - a failing run records its reason, and the rest continue
  - a worker restart mid-study requeues the interrupted run at the front
  - sensitivity reports the spread
- [ ] T128 [US4] **Owner (main PC)**: work through quickstart row 4. Then run SC-004: 18 standard single-crown runs
  (6 lengths × 3 densities) must finish within 10 h. Record the result in `docs/setup/performance.md`. Note the
  margin: at the 30 min SC-003 limit, 18 runs already take 9 h, so compare the per-run time from T059's calibration
  with the 33 min that SC-004 allows.

**Checkpoint**: studies run unattended overnight.

---

## Phase 8: User Story 5: prove the engine is right (Priority: P5)

**Goal**:

- 3 to 5 reproductions of published dental implant studies, with tolerances fixed before the first run
- tolerance history enforced
- mesh checks
- a verification page and the publish lock

**Independent Test**: run the suite. Each case shows its expected value, computed value, tolerance and pass or fail.
Publishing stays locked while any required case fails (quickstart row 5).

- [ ] T129 [US5] Implement `engine/src/dentalsim/verification/builders/published.py`:
  - a case model can be one or more scenario definitions (variants) built from the blocks
  - the `ratio` and `relative` comparers work across variants, for example the crestal-strain ratio between two
    diameters
- [ ] T130 [US5] **Owner + Claude research**:
  1. Choose 3 to 5 studies from the research R15 candidates.
  2. Add their sources to the library and pass the source check.
  3. Write `content/verification/cases/published-<first-author>-<year>.yaml` for each. Compared quantities are ratios,
     trends or region quantities only; each expected value cites its table or page, with a tolerance. The case's
     material, bone and load inputs are the study's own, cited in the file (FR-035).
  4. Commit the files before the first run.
- [ ] T131 [US5] Enforce the tolerance history in `engine/src/dentalsim/verification/cases.py` (FR-035):
  - compare each case file with its first committed version (`git log --follow`)
  - a changed tolerance without a matching `tolerance_changes` entry fails `dentalsim check`
  - add `engine/tests/unit/test_tolerance_history.py`
- [ ] T132 [US5] Implement the mesh check (FR-037):
  - `engine/src/dentalsim/verification/mesh_check.py`: solves at two detail levels; computes the relative change of
    each key number; passes or fails against the conventions limit; stores a MeshCheck record
  - `dentalsim mesh-check` in `engine/src/dentalsim/commands/mesh_check.py`
  - `POST /api/scenarios/{id}/mesh-check` in `studio/server/src/dentalsim_studio/api/verification.py`, run by
    `studio/server/src/dentalsim_studio/worker/handlers/mesh_check.py`
- [ ] T133 [US5] Complete the verification routes in `studio/server/src/dentalsim_studio/api/verification.py`, with
  `studio/server/src/dentalsim_studio/worker/handlers/verification.py`:
  - `GET /api/verification/cases`
  - `POST /api/verification/run`: a job that updates capability status (FR-062)
  - `GET /api/verification/reports/{id}`
  - add `engine/src/dentalsim/verification/gate.py`: `required_cases_passing()` names the failing required cases
    (FR-036)
  - the runner also measures `preview_accuracy` on the published-study cases; previews then show that figure, naming
    the case set (FR-017)
- [ ] T134 [P] [US5] Implement `studio/ui/src/pages/Verification.tsx`:
  - a **Run all** button
  - per case: expected value, computed value, tolerance, and pass or fail
  - tolerance history and the list of reports
  - mesh-check results per scenario
- [ ] T135 [US5] Add `engine/tests/benchmark/test_published_studies.py`: every chosen study reproduces within its
  recorded tolerance
- [ ] T136 [US5] **Owner (main PC)**:
  1. Run `uv run dentalsim verify`. SC-006: every textbook case passes, and at least 3 published studies reproduce.
  2. Run a mesh check on the single-crown scenario, `standard` against `fine`.
  3. Confirm the provisional "detailed" timings (FR-018): if `standard` passes, SC-003, SC-004 and SC-013 stand as
     recorded in T098, T107 and T128. Otherwise, set the lowest passing level as the detailed level and repeat those
     timings at it.
  4. Keep the reports, and record the outcome in `docs/setup/verification.md` and `docs/setup/performance.md`.

**Checkpoint**: the engine verification gate in the constitution is met.

---

## Phase 9: User Story 6: export material for lectures and records (Priority: P6)

**Goal**: figures, films, slide loops, a ready-made PowerPoint slide, a PDF run report and spreadsheets, each
regenerable from the stored run with identical content.

**Independent Test**: from one finished run, produce every export type and open each in its program (quickstart
row 6).

- [ ] T137 [US6] Implement `engine/src/dentalsim/exports/base.py` (research R16, FR-044):
  - the Export record, with a versioned template (`template_version` per kind)
  - every export is built from stored results and the run record only
  - `regenerate()` compares numbers and text exactly, and images by pixel difference below a fixed threshold
- [ ] T138 [US6] Implement `engine/src/dentalsim/exports/render.py`:
  - PyVista off-screen rendering of stored fields with the conventions colour scales
  - overlays: legend, scale bar, units, magnification, source line and the teaching-only notice
  - shared scales for comparisons
- [ ] T139 [P] [US6] Implement `engine/src/dentalsim/exports/figure.py`: a 300 dpi PNG (FR-038)
- [ ] T140 [P] [US6] Implement `engine/src/dentalsim/exports/film.py` and
  `engine/src/dentalsim/exports/slide_loop.py` (FR-039, FR-040):
  - frames encoded with imageio-ffmpeg: H.264, yuv420p, 1920×1080, 30 fps, `+faststart`
  - films of results and comparisons
  - slide loops of 6–15 s that loop seamlessly (the last frame matches the first)
- [ ] T141 [P] [US6] Implement `engine/src/dentalsim/exports/pptx.py` (FR-040): a python-pptx 16:9 slide with the loop
  as a movie with a poster frame, plus the title, one-line lesson and source
- [ ] T142 [P] [US6] Implement `engine/src/dentalsim/exports/report.py` and
  `engine/src/dentalsim/exports/templates/run_report.typ`: a Typst PDF listing every setting, every library entry and
  version with its sources, the key results with their statistic labels, the limits, the verification status and the
  notice (FR-042)
- [ ] T143 [P] [US6] Implement `engine/src/dentalsim/exports/spreadsheet.py` (FR-043): an `.xlsx` with sheets for
  settings, library versions, key numbers and region statistics, plus `.csv` files
- [ ] T144 [US6] Wire up exports in the studio and command line, with output in `<data>/exports/`:
  - `studio/server/src/dentalsim_studio/api/exports.py`: `POST /api/exports`, `GET /api/exports/{id}`,
    `GET /api/exports/{id}/download`, and `POST /api/exports/{id}/regenerate`
  - `studio/server/src/dentalsim_studio/worker/handlers/export.py`
  - `dentalsim export` and `dentalsim regenerate` in `engine/src/dentalsim/commands/export.py`
- [ ] T145 [P] [US6] Implement `studio/ui/src/pages/Exports.tsx`:
  - choose run(s) or a comparison, the kind, and options (title, lesson)
  - job progress and download
  - regenerate, showing whether the result is identical
- [ ] T146 [US6] Add `engine/tests/integration/test_exports_regenerate.py`:
  - every kind regenerates with identical content
  - the figure carries the notice
  - the report lists every library version
  - the key numbers in the `.xlsx` equal the stored ones
- [ ] T147 [US6] **Owner**: open the `.pptx` in PowerPoint and confirm the clip plays without conversion (SC-009). Also
  open the figure, film, PDF and `.xlsx`. Record the results in `docs/setup/exports-check.md`.

**Checkpoint**: teaching material comes out of the studio ready to use.

---

## Phase 10: User Story 7: publish a scenario to the website (Priority: P7)

**Goal**:

- the publication record and the checklist
- interactive page data
- the versioned package in contracts/published-package.md
- unpublish and republish history
- regression references

**Independent Test**: publishing with one checklist item missing is refused, naming the item. Complete it and
publish. The package matches the contract, the interactive solved cases equal the stored runs, and the history is
kept (quickstart row 7).

- [ ] T148 [US7] Implement `engine/src/dentalsim/publish/record.py` and
  `studio/server/src/dentalsim_studio/db/repos/publication.py`, the PublicationRecord:
  - the assumptions note (FR-061)
  - the verification note (FR-060), filled in automatically where possible (mesh check or documented carry-over,
    convergence, verification report id), plus a literature comparison written by the owner
  - the owner's sign-off, and the frame review `{desktop_ok, phone_ok}`
- [ ] T149 [US7] Implement `engine/src/dentalsim/publish/checklist.py`, with every item in data-model.md (FR-045). A
  refusal lists every failing item.
  - the verification note and the assumptions note are complete
  - the owner has signed off, and the frame review is done on desktop and on a phone
  - a complete test gate (not "incomplete", T056) passed for the current engine commit
  - the notice is present, and every input is an approved library version
  - no run carries a blocking flag; this item calls `Run.is_publishable()` (T024), so the flag list lives in one
    place: `out_of_date`, `uses_rejected_entry`, `outside_published_range`, `dev_build` or `needs_reverification`
  - the published scenario carries neither `needs_reverification` (FR-051) nor `needs_review` (FR-062)
  - the required verification cases pass (FR-036)
  - brand rules (FR-049): the text is scanned against `content/brand-names.txt` unless `brand_comparison` is
    complete. **Claude research, owner approves**: create `content/brand-names.txt` (implant, attachment and
    material brand names, one per line, case-insensitive) as part of this task
- [ ] T150 [US7] Implement `engine/src/dentalsim/publish/interactive.py` (FR-063, research R18):
  - bite angle and force by superposition of the unit cases; discrete controls from solved runs
  - other continuous controls by linear interpolation, with the error measured on a held-out case
  - ranges clamped to the solved cases (`no_extrapolation`)
  - an automatic check that every solved case equals its stored run (SC-015)
  - add `dentalsim interactive-data` in `engine/src/dentalsim/commands/publish.py` and
    `studio/server/src/dentalsim_studio/worker/handlers/interactive_data.py`
- [ ] T151 [US7] Implement `engine/src/dentalsim/publish/package.py` (research R19):
  - write `content/scenarios/<slug>/v<N>/package.json` exactly as contracts/published-package.md defines it
  - stills and `.glb` files up to the conventions `max_committed_asset_bytes` (1 MB proposed) go in `assets/`; larger
    files and all films go in `<data>/published-media/`, named by SHA-256 with their size
  - never overwrite an existing version; record publish, republish and unpublish in the history
  - write the regression references to `engine/tests/regression/references/<slug>-v<N>.json`
- [ ] T152 [P] [US7] Add `engine/src/dentalsim/publish/package_schema.json` (JSON Schema of `dentalsim.package/1`) and
  `engine/tests/unit/test_package_contract.py`:
  - the built package validates
  - every key number has a statistic, and there are no peaks
  - visuals shown together share their scales
  - there are no brand names unless `brand_comparison` is filled in
- [ ] T153 [US7] Implement `engine/src/dentalsim/publish/flags.py` (FR-051): at start-up and on
  `dentalsim conventions check`, flag every published scenario with an older conventions version
  `needs_reverification`, and block republishing it until it is re-verified. Re-verified means: every run in the
  publication record is re-run under the current conventions version, and the verification note is renewed from
  those runs. The flag is cleared only when that republication succeeds (FR-051). Add these cases to
  `studio/server/tests/test_api_publish.py` (T157): republishing is refused while the flag is set, and succeeds and
  clears it after the re-runs
- [ ] T154 [US7] Implement `studio/server/src/dentalsim_studio/api/publish.py`, and add `dentalsim publish` and
  `unpublish` to `engine/src/dentalsim/commands/publish.py`:
  - `GET` and `PUT /api/scenarios/{id}/publication`, and `GET /api/scenarios/{id}/publication/checklist`
  - `POST /api/scenarios/{id}/interactive-data`
  - `POST /api/scenarios/{id}/publish`: `publish_refused` lists every failing item
  - `POST /api/published/{slug}/unpublish`
- [ ] T155 [P] [US7] Implement `studio/ui/src/pages/Publish.tsx`:
  - **Record form**: title, slug, branch, type, situations, keywords, one-sentence message, explanation, assumptions
    and verification note fields, references, access level, brand-comparison fields
  - **Checklist**: every item with its state
  - **Frame review**: a desktop frame and a phone frame (390×844)
  - **Actions**: sign-off; Publish, showing the refusal list; history; unpublish
- [ ] T156 [US7] Add `engine/tests/regression/test_published_reference_numbers.py` (SC-007, constitution): for every
  reference file, re-run the scenario from the package provenance; the key numbers must agree within 0.1%
- [ ] T157 [US7] Add `studio/server/tests/test_api_publish.py`:
  - a missing sign-off is refused and named
  - a `dev_build` run is refused
  - a complete checklist gives a valid package v1
  - editing the scenario leaves v1 unchanged
  - unpublish then republish gives v2, with the history kept
- [ ] T158 [US7] **Owner**: publish the first scenario through the constitution publication gate (notes, sign-off,
  frame review on desktop and phone, tests, notice, approved inputs), and commit `content/scenarios/<slug>/v1/`.
  Once the checklist is complete, publishing takes under 15 minutes (SC-010).

**Checkpoint**: the first verified scenario is published for the website (feature 001) to read.

---

## Phase 11: User Story 8: make a promo clip (Priority: P8)

**Goal**: a vertical 15–45 s clip from a published scenario, with captions, a generated voice, the notice and an end
card.

**Independent Test**: from an unpublished scenario, a promo clip is refused. From a published one, the clip is
vertical and 15–45 s long, with captions, voice, notice and end card (quickstart row 8).

- [ ] T159 [US8] Implement `engine/src/dentalsim/exports/voice.py`:
  - a voice interface, implemented with Kokoro-82M running locally (extra `promo`)
  - returns the audio and the duration of each sentence
  - records the model name, version and licence (Apache-2.0)
- [ ] T160 [US8] Implement `engine/src/dentalsim/exports/promo.py` (FR-041, research R17):
  - refused unless the scenario is published
  - re-renders 1080×1920 frames from the published version's stored results
  - burns in captions per sentence (Pillow), timed by the audio
  - adds the teaching-only notice and an end card with the platform name and link
  - encodes H.264 with AAC; a script that gives a clip outside 15–45 s is refused, saying why
  - stores a PromoClip record with the approved script
- [ ] T161 [US8] Add `POST /api/published/{slug}/promo` to `studio/server/src/dentalsim_studio/api/publish.py`,
  `studio/server/src/dentalsim_studio/worker/handlers/promo.py`, and `dentalsim promo <slug> --script` in
  `engine/src/dentalsim/commands/publish.py`
- [ ] T162 [P] [US8] Implement `studio/ui/src/components/PromoDialog.tsx`, opened from the Publish page: a script
  editor, an approve check box, a preview player and download
- [ ] T163 [US8] Add `engine/tests/integration/test_promo.py`:
  - an unpublished scenario is refused
  - a published one gives a 1080×1920 clip of 15–45 s with an audio stream and the notice in the captions
  - the voice licence is recorded

**Checkpoint**: Stories 1–8 are delivered on bonded-elastic physics.

---

## Phase 12: User Story 9: unlock new physics (Priority: P9; framework only in this feature)

**Goal**: finish the lock and unlock cycle, which needs publishing to exist (FR-062).

Screw preload, the periodontal ligament, contact and fatigue are each a separate later feature, in that order. Each
gets its own spec, plan and tasks, with its benchmarks, library entries, blocks and templates (research R21).

**Independent Test**: a test capability stays locked until its benchmark passes, then unlocks with the report linked.
When it fails a re-run, it locks again and the published scenarios that depend on it are flagged `needs_review`.

- [ ] T164 [US9] Complete FR-062 in `engine/src/dentalsim/verification/runner.py` and
  `engine/src/dentalsim/publish/flags.py`: when a capability goes back to `awaiting_benchmark`, flag `needs_review` on
  every published scenario whose runs used a block or result type that needs it, and emit `capability.changed`.
  `needs_review` blocks republishing (T149) until the capability is ready again and the owner records the review in
  the publication record; the flag is cleared only by that republication (FR-062)
- [ ] T165 [US9] Add `engine/tests/integration/test_capability_lock_cycle.py`, using a capability registered only in
  the test, with one dummy case. Check this cycle:
  1. While awaiting, previews are refused and its results are locked.
  2. When the case passes, the capability is ready.
  3. When a re-run fails, it is awaiting again, and the dependent published scenario is flagged `needs_review`.
- [ ] T166 [P] [US9] Write `docs/extending.md`:
  - step by step, how to add a physics capability: benchmark cases first, then library entries, the capability
    registration, blocks, templates and result types (contracts/scenario-format.md)
  - the order of the Story 9 increments

**Checkpoint**: the studio is ready for the Story 9 physics features.

---

## Phase 13: Polish and cross-cutting concerns

- [ ] T167 [P] Add Playwright smoke tests in `studio/ui/tests/e2e/smoke.spec.ts` (research R20): sign in, build from a
  template, run a preview, compare two runs
- [ ] T168 [P] Add Vitest tests in `studio/ui/tests/unit/colourScale.test.ts` (colours match the conventions) and
  `studio/ui/tests/unit/LibraryValueField.test.tsx` (the outside-range mark appears)
- [ ] T169 Harden security in `studio/server/src/dentalsim_studio/auth/`:
  - check the cookie flags, rate limit and idle timeout
  - add an `Origin` check on every route that changes data
  - make sure no password or session id is ever logged
- [ ] T170 **Owner**: run the SC-011 check on a phone on mobile data (the studio must fail to open), and confirm the
  router has no port forwarding. Record both in `docs/setup/network.md`.
- [ ] T171 [P] Write `docs/setup/main-pc.md` and `docs/setup/laptop.md` from quickstart.md: solver, firewall,
  certificate trust and backup routine
- [ ] T172 On the main PC, run `uv run dentalsim check`, including the benchmark and regression tests, and the full
  `specs/002-simulation-studio/quickstart.md` walkthrough for Stories 1–8. Fix every failure.
- [ ] T173 **Owner**: time SC-001 (opening the studio to starting a detailed run from a template in under 5 min) and
  SC-008 (comparison ready in under 1 min), and record both in `docs/setup/performance.md`

---

## Phase 14: Rented solver machine (optional; FR-064, research R23)

**Goal**: send heavy runs that do not fit the main PC to a rented machine over SSH, with only the solver step
moving there. Start this phase only when T098, T107 or a study shows the main PC is too small or too slow.

**Independent Test**: on a rented machine, `dentalsim solver check --host NAME` and `dentalsim verify --host NAME`
pass; a detailed run sent there gives the same key numbers (within 0.1%) as the same run on the main PC; cutting
the connection mid-run fails the run cleanly with its reason and stores nothing.

- [ ] T174 [P] Implement `engine/src/dentalsim/solvers/remote.py`:
  - solver hosts from `<data>/solver-hosts.yaml` (name, SSH target, private-key path in the owner's profile, ccx path,
    threads, memory GB, platform); never stored in git or the database
  - a remote runner with the same interface as `run_ccx`: copy the work folder with `scp`, run ccx over `ssh` with the
    thread variables, stream its output for progress and the PaStiX residual, copy `.frd`, `.dat` and the log back,
    delete the remote folder; cancel ends the remote process tree; a lost connection raises `SolverFailed`
  - the remote build id: version and equation solver from a probe run there, SHA-256 computed there
- [ ] T175 Add `--host NAME` to `dentalsim solver check`, `dentalsim verify` and
  `python -m dentalsim.estimate.calibrate` (coefficients per solver build already, research R8)
- [ ] T176 Extend the gate and the run path:
  - `engine/src/dentalsim/physics/registry.py` and `gate.py`: a capability is ready for a solver build only when a
    passing report was made with that build; runs on an unverified build are refused, naming the benchmark
  - `engine/src/dentalsim/pipeline.py` and `studio/server/src/dentalsim_studio/worker/handlers/run.py`: a run's
    `solver_host` (default: the main PC) picks the runner, the estimate and the memory limit; the run records it
  - previews always run on the main PC
- [ ] T177 [P] In the interface: list the solver hosts with their build and verification status on the Physics page,
  and let the owner choose the main PC or a rented machine in `EstimateDialog.tsx` and the study dialog
- [ ] T178 Add `engine/tests/unit/test_remote_runner.py` (ssh and scp replaced by local stand-ins: streaming, cancel,
  lost connection, clean-up) and `studio/server/tests/test_remote_runs.py` (an unverified host is refused; a failed
  remote run stores nothing partial)
- [ ] T179 **Owner**: rent a machine; install OpenSSH server and `ccx_static.exe` (Windows) or build ccx 2.23 with PaStiX
  (Linux); add it to `solver-hosts.yaml`; run `solver check`, `verify` and the calibration with `--host`; compare one
  detailed run with the main PC; record everything in `docs/setup/rented-solver.md`

---

## Dependencies and execution order

### Phase dependencies

| Phase | Depends on |
|---|---|
| **Setup** (Phase 1) | Nothing |
| **Foundational** (Phase 2) | Setup. Blocks every story. Its owner tasks (T011, T022, T040, T059) are on the critical path. |
| **US1 slice 1a** (Phase 3) | Foundational. The MVP. |
| **US1 slice 1b** (Phase 4) | Slice 1a |
| **US2, US3, US4, US5** (Phases 5–8) | Slice 1a. They can proceed in parallel after it, or in priority order. |
| **US6** (Phase 9) | Slice 1a (stored runs). Comparison exports also use US3's `post/compare.py`. |
| **US7** (Phase 10) | US5 (verification gate and mesh check), US6 (stills, films, `render.py`), US2 (rejection flags) |
| **US8** (Phase 11) | US7 (only published scenarios) and US6 (`render.py`) |
| **US9 framework** (Phase 12) | US7 (published scenarios to flag) |
| **Polish** (Phase 13) | Every story you choose to deliver |
| **Rented solver machine** (Phase 14, optional) | Slice 1a (pipeline T081, verification T071–T075, run handler T088); start only when the main PC proves too small or slow |

### User story dependency graph

```text
Setup → Foundational → US1 (1a) ─┬─→ US1 (1b)
                                 ├─→ US2 ─────────────┐
                                 ├─→ US3 ──→ US6 ─────┤
                                 ├─→ US4              ├─→ US7 ─┬─→ US8
                                 └─→ US5 ─────────────┘        └─→ US9 framework
```

The stories are less independent than in a typical feature: US1's runs are the input to everything after it. Each
story can still be tested on its own once US1 (1a) exists.

### Within each story

1. Engine code before API.
2. API before interface.
3. Tests after the code they cover (regression-based).
4. The owner's acceptance task last.
5. Benchmark and verification case files are committed **before** the first run of those cases (T074, T130).

### Critical owner path

| Order | Task | What |
|---|---|---|
| 1 | T011 | Solver |
| 2 | T022 | Conventions approved |
| 3 | T040 | Seed library approved |
| 4 | T059 | Calibration |
| 5 | T067, T074 | Single-crown template defaults; textbook tolerances |
| 6 | T098 | Slice 1a acceptance |
| 7 | T107, T128, T130, T136, T147 | Story acceptances; choosing the published studies (T130) |
| 8 | T158 | First publication |
| 9 | T170, T173 | Network and timing checks |

---

## Parallel examples

### Foundational

```text
Task: "T012 Implement solvers/calculix/build.py"
Task: "T013 Implement solvers/calculix/inp_writer.py"
Task: "T014 Implement solvers/calculix/runner.py"
Task: "T015 Implement frd_reader.py and dat_reader.py"
# then, independently:
Task: "T023 model/settings.py + model/scenario.py"
Task: "T024 model/validity.py + results.py + run.py"
Task: "T026 store/hashing.py"
Task: "T027 store/cas.py"
Task: "T034 library/models.py"
Task: "T035 library/yaml_io.py"
Task: "T036 library/source_check.py"
Task: "T042 auth/network_guard.py"
Task: "T043 auth/password.py + sessions.py"
Task: "T044 auth/certificate.py"
Task: "T045 events/bus.py + api/events.py"
```

### User Story 1 (slice 1a)

```text
# after T060 (block base):
Task: "T061 blocks/jaw_segment.py"
Task: "T062 blocks/implant.py"
Task: "T063 blocks/abutment.py"
Task: "T064 blocks/crown.py"
# after T070 (mesh):
Task: "T071 verification/cases.py + runner.py"
Task: "T072 verification/builders/textbook.py"
Task: "T073 solvers/skfem/crosscheck.py"
Task: "T076 post/fields.py"
Task: "T077 post/superpose.py"
Task: "T078 post/statistics.py"
Task: "T080 post/gltf.py"
# interface, once the API contract routes exist:
Task: "T090 viewer/*"
Task: "T091 SettingsForm + LibraryValueField"
Task: "T092 LoadEditor"
Task: "T095 EstimateDialog + Queue page"
Task: "T097 Physics page"
```

### User Story 1 (slice 1b)

```text
Task: "T099 blocks/pontic.py"
Task: "T100 blocks/splinting_connector.py"
Task: "T101 blocks/cantilever_extension.py"
Task: "T102 block unit tests for 1b"
```

### User Story 2

```text
Task: "T110 pages/Library.tsx"
Task: "T111 components/AddEntryForm.tsx"
```

### User Story 3

```text
Task: "T115 model/diff.py"
Task: "T116 post/compare.py"
# after T117:
Task: "T118 pages/Projects.tsx"
Task: "T119 duplicate-and-change in Builder.tsx"
```

### User Story 4

```text
Task: "T123 study.py"
Task: "T126 StudyDialog + Study page"
```

### User Story 5

```text
Task: "T134 pages/Verification.tsx"
# while T131/T132 are in progress
```

### User Story 6

```text
# after T137–T138:
Task: "T139 exports/figure.py"
Task: "T140 exports/film.py + slide_loop.py"
Task: "T141 exports/pptx.py"
Task: "T142 exports/report.py + run_report.typ"
Task: "T143 exports/spreadsheet.py"
```

### User Story 7

```text
Task: "T152 package_schema.json + contract test"
Task: "T155 pages/Publish.tsx"
# alongside T150–T151
```

### User Story 8

```text
Task: "T162 PromoDialog.tsx"
# alongside T160
```

### User Story 9

```text
Task: "T166 docs/extending.md"
# alongside T164–T165
```

---

## Implementation strategy

### MVP first (plan stages 0 and 1a)

1. Phase 1 Setup, then Phase 2 Foundational. **Stage gate**: conventions check, solver smoke test, seed library
   approved.
2. Phase 3 (US1, slice 1a). **Stop and validate** with T098: benchmarks pass, preview ≤ about 13 s, detailed run ≤ 30 min.
3. The owner can now build and study single implant crowns. That is the first usable studio.

### Incremental delivery (plan delivery stages)

| Step | Phase | Delivers |
|---|---|---|
| 1 | Phase 4 | Slice 1b: every implant-supported fixed scenario |
| 2 | Phases 5–7 | US2, US3, US4 (plan stage "2–4"): full library, organising and comparing, overnight studies |
| 3 | Phase 8 | US5 (plan stage 5): published-study reproductions, which unlock publishing |
| 4 | Phases 9–11 | US6, US7, US8 (plan stage "6–8"): the first scenario published, then promo clips |
| 5 | Phase 12, then Story 9 | The framework is finished. Each Story 9 physics then starts with `/speckit-specify` as its own feature. |

Each step ends with the owner's acceptance task for that story.

### Working alone with Claude

- Work through the phases in order.
- Inside a phase, hand [P] groups to parallel subagents. Each [P] task touches its own files.
- Commit after each task or logical group (constitution: "commit per logical group").
- Run the complete `uv run dentalsim check` on the main PC before every merge. On the laptop, `--quick` is for
  feedback only; an "incomplete" result never allows a merge.

---

## Notes

- **New top-level modules**: `pipeline.py`, `persistence.py`, `study.py`, `backup.py`, `config.py`, `version.py` and
  `commands/` extend plan.md's source tree. They hold orchestration and wiring, not science.
- **Contract change**: T086 adds `POST /api/scenarios/{id}/geometry` to `contracts/studio-api.md`. No other change to
  a contract is planned. (contracts/cli.md already lists `dentalsim check --quick` and the "incomplete" result, from
  the 2026-11-05 analysis.)
- **Values in the code**: no value from the old `fea-implant-teaching/` project is copied. Its verified numbers may be
  used only as extra benchmark cases (constitution, Scope).
- **Owner approval comes first**: every number comes from approved library entries or committed case files, and is
  never hard-coded in the engine (Principles II and VIII).
