# Research: Simulation Studio (Phase 0)

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Date**: 2026-11-05

Each item gives the decision, why it was chosen, and what else was considered. Every "NEEDS CLARIFICATION" from
the Technical Context is resolved here. Items marked **owner confirms** are defaults the owner approves when the
conventions document or library is written; they do not block planning.

---

## R1. Languages and environment

- **Decision**:
  - The engine, studio server, worker and command line are written in **Python 3.12**, managed with **uv**:
    one workspace and one `uv.lock`.
  - The browser interface uses **TypeScript** with **Node 24 LTS**, needed only to build it.
  - At run time the main PC needs Python, the CalculiX solver and the built interface files.
- **Rationale**:
  - The scientific stack (gmsh, NumPy, SciPy, VTK) is Python-first.
  - uv (0.9 is installed on the laptop) gives fast, locked, reproducible environments on Windows (Principle VI).
  - A typed browser interface is needed for the 3D builder (see R11).
- **Alternatives considered**:
  - Conda: heavier, and nothing in the stack needs it.
  - An all-Python interface (R11): rejected.

## R2. Geometry and meshing

- **Decision**: use **gmsh 4.15** through its Python API, with the OpenCASCADE kernel.
  - **Shapes**: each block builds its own solid from its settings:
    - jaw-segment cross-section swept along an arch curve, with a cortical shell, nerve canal and sinus floor
    - implant body with an axisymmetric thread profile (V, buttress or square)
    - abutment and screw access
    - parametric crown, pontic and connector shapes
  - **Bonded contacts**: the assembly is joined with `BooleanFragments`, so parts that touch share nodes. This
    is what "bonded" means for this physics.
  - **Elements**: quadratic tetrahedra (10 nodes).
  - **Mesh refinement**: distance and threshold fields refine the mesh at implant threads, the crest and
    connectors.
  - **Reproducibility**: meshing runs single-threaded with a fixed random seed, and the gmsh version is
    recorded per run.
  - **Contact, later**: contact physics (Story 9) will keep separate, non-fragmented surfaces where needed.
- **Rationale**:
  - One pip-installable tool covers both CAD and meshing on Windows.
  - It is open source (GPL with a linking exception), and the old project shows it handles dental shapes.
- **Alternatives considered**:
  - CadQuery for the shapes, then gmsh for meshing: more CAD power, but a second large dependency. Revisit if
    crown shapes need free-form surfaces gmsh cannot make.
  - Netgen: weaker scripting and size control.
  - Helical threads: costly to mesh, and most published models use axisymmetric threads. They can become a
    block option later.

## R3. Finite element solver

- **Decision**: use **CalculiX ccx 2.23** (GPL-2; stable release 19 Oct 2025) as the single solver for every
  physics stage.
  - **Build** (revised 2026-11-05 after measuring on the main PC): the open-source Windows build
    `ccx_static.exe` from dhondt.de, with the **PaStiX** sparse direct solver (and Scotch, hwloc and OpenBLAS,
    all open source), multithreaded. PARDISO is not used: it needs Intel MKL, which is free but not open source
    (constitution, Scope), and PaStiX was faster on detailed models anyway (table below).
  - **How PaStiX runs in ccx 2.23**: it factors in single precision, then refines the solution in double
    precision (GMRES) until the residual is below 1e-12, and switches itself to full double precision if that
    refinement does not converge. It prints the error of each refinement iteration, which gives the relative
    residual FR-023 asks for.
  - **How the engine drives it**:
    - writes a solver input file (`.inp`)
    - runs `ccx` as a separate process
    - reads nodal fields from `.frd` and integration-point stress, strain and element volumes from `.dat`
  - **Solver identity**: the binary's SHA-256 and version string form the solver build id stored with every run.
  - **Fallbacks**, each recorded as a different solver build:
    - the same program with the SPOOLES solver (open source, about 3 times slower)
    - the Ubuntu `calculix-ccx` package under WSL2 (version 2.21, not 2.23)
  - **Measured on the main PC** (10 physical cores, 31.8 GB; 8 solver threads; a 20 mm cube of 10-node
    tetrahedra, one unit load case, time including assembly and output):

    | DOF | PARDISO (MKL) | PaStiX (`ccx_static.exe`) | SPOOLES |
    |---|---|---|---|
    | 48 k | 3.5 s, 0.35 GB | 3.5 s, 0.43 GB | 4.3 s, 0.39 GB |
    | 165 k | 16.6 s, 1.7 GB | 19.7 s, 2.0 GB | 49.5 s, 1.8 GB |
    | 387 k | 110 s, 5.4 GB | 69 s, 6.1 GB | — |
    | 758 k | 308 s, 13.5 GB | 154 s, 14.4 GB | — |

    Every solver balanced the support reactions against the load to about 1e-14 (relative); PaStiX's final
    refinement error was 1e-16 to 6e-14.
- **Rationale**:
  - Its input language is Abaqus-like, which matches how most published dental FEA is described (linear
    elastic, bonded, 10-node tetrahedra). That makes the 3–5 published reproductions straightforward.
  - It already has everything Story 9 needs, so no physics has to be written from scratch:
    - frictional surface-to-surface contact (dentures on mucosa, attachments, micromotion)
    - bolt pretension (`*PRE-TENSION SECTION`, for screw preload)
    - hyperelastic materials (ligament, mucosa)
  - It is free to use and the results can be published and monetised; no licence touches the outputs.
  - Running it as a separate process keeps a GPL binary apart from our code.
- **Alternatives considered**:
  - **FEniCSx (dolfinx)**: native Windows conda-forge builds exist in 2026 (0.10, 0.11). But contact is still an
    add-on, compilation needs MSVC, and published-study inputs would have to be rewritten as weak forms.
    Rejected as primary.
  - **scikit-fem** (the old project's solver): pure Python and easy to install, but contact, pretension and
    hyperelasticity would have to be built and benchmarked by us. **Kept** as an independent second solver for
    the textbook benchmarks: two unrelated codes agreeing strengthens verification.
  - **FEBio** (MIT): the best option for biphasic and viscoelastic soft tissue. Kept as a candidate second
    solver behind the same adapter interface if the ligament or mucosa needs those models (decided in Story 9
    planning).
  - **Code_Aster, MOOSE**: Linux-centred and heavy on Windows.
  - Commercial codes are excluded by the constitution.
- **Risk and first task**: get and smoke-test the solver build on the main PC before any engine work (done
  2026-11-05 with `ccx_static.exe`), and calibrate R8 with it (T059).
- **What the solver does not report**: ccx 2.23 never stops on a singular stiffness matrix. A cube held only
  along one edge "solved" with no message and displacements of 10⁹–10¹⁰ mm under every equation solver. The
  checks that catch it are the engine's own (FR-023): force balance (an error of 57% to 874% here, but that
  depends on the hinge's orientation), the energy check (work of the load / twice the strain energy: 1.0000003
  for the supported cube, about −10¹¹ for the hinged one) and, with PaStiX, the residual (2e-6 after the
  50-iteration cap, against about 1e-14 normally). A part that touches nothing carries no load and is caught
  only by a connectivity check before the solve.

## R4. Linear load cases and superposition

- **Decision**: for bonded-elastic runs, solve unit load cases and combine them afterwards.
  - Each contact point gets 3 unit cases (1 N along x, y and z), as separate solver steps.
  - Any force magnitude and direction is a linear combination of these:
    `u = F (cx·u_x + cy·u_y + cz·u_z)`, and stresses and strains combine the same way.
  - Von Mises and principal values are computed **after** combining.
- **Rationale**: this is exact in linear elasticity. One run then covers every bite angle and force, which makes
  studies over angle or force and interactive page data (FR-063) cheap and free of interpolation error.
- **Alternatives considered**: one solve per load. That is simpler, but a study over 7 angles would need 7 times
  the runs.
- **Limit**: superposition is invalid for contact, preload and other nonlinear physics. The physics capability
  declares whether it is linear, and interactive data then falls back to solved cases (R18).
- **Cost**: ccx 2.23 factors the stiffness matrix again for every unit case: its linear static driver solves
  one right-hand side per step, although the PaStiX and PARDISO interfaces already accept several (checked in
  `linstatic.c`; 3 cases took 2.8 times as long as one). So each contact point costs 3 factorisations. If the
  single-crown acceptance run (T098) misses the 30-minute target, the main lever is a ccx build patched to
  factor once and solve every unit case (open source, GPL; we would build and publish it), before buying
  memory or renting compute (R23).

## R5. Reported values and regions

- **Decision**:
  - **Where values come from**: reported values are computed from integration-point values, weighted by the
    integration-point volume (element volume / 4 for 10-node tetrahedra).
  - **Statistics**: region mean and volume-weighted 95th percentile (Principle II). Nodal peaks are never
    reported.
  - **Regions**: defined as geometric tests on integration-point positions plus the material group. Examples:
    "cortical bone within 1.5 mm of the implant surface and within 2 mm below the crest". Because they are not
    mesh partitions, the same region exists at every detail level, which the mesh check needs.
  - **Worse side**: around each implant or tooth, the crestal ring is split into mesial, distal, buccal and
    lingual sectors. The sector with the highest mean is reported and named.
- **Rationale**: these values do not depend on the mesh, they agree across detail levels, and they match what the
  constitution and FR-024 require.
- **Alternatives considered**: nodal averaged values, which are sensitive to the mesh near corners; physical-group
  regions, which would tie regions to how the mesh is built.

## R6. Conventions document (FR-050, SC-014)

- **Decision**: there is one file, `docs/conventions.md`. It holds prose for people plus one fenced
  ```` ```yaml conventions ```` block that the engine reads at start-up.
  - The block carries a semantic version.
  - If the block is missing or invalid, nothing runs.
  - Tests check that every block type, result type and export uses it.
- **Proposed defaults (owner confirms)**:
  - **Units**: mm, N, MPa, tonne, s; strain reported in microstrain (µε).
  - **Frame**:
    - origin at the crest centre of the first implant or tooth site
    - +z occlusal along the long axis of the reference implant
    - +x mesial
    - +y buccal (labial)
    - right-handed
  - **Load angle**: measured from +z (the axial bite). Positive tilts the force toward lingual or palatal
    (buccal cusp load). An azimuth is given for loads outside the buccolingual plane.
  - **Regions**: the crestal ring (R5) with sector rule for the worse side; implant neck (top 2 mm of the
    implant); and whole-component regions for the abutment, crown, connector and screw.
  - **Bone strain**: von Mises equivalent strain in µε for the Frost colours, with maximum and minimum principal
    strain also stored.
  - **Frost windows**: the window edges come from approved library entries, not from the code. The scale is 4
    categorical colours, colour-blind safe, labelled "teaching reference":
    - disuse
    - adapted
    - mild overload
    - pathological overload
  - **Strength comparison**:

    | Material type | Stress measure | Compared with |
    |---|---|---|
    | Metals (titanium alloys, CoCr) | Von Mises stress | Yield strength |
    | Brittle materials (zirconia, lithium disilicate, feldspathic, composites) | Maximum principal tensile stress | Flexural strength |
    | Bone | Strain, as above | Not compared with strength |

  - **Mesh-check limit**: 5% change in every key result between two detail levels (FR-037).
- **Rationale**: keeping the prose and the machine values in one file meets "a single conventions document"
  literally, so the two cannot drift apart.
- **Alternatives considered**: a separate YAML file plus a doc, which would be two sources; code constants, which
  the owner cannot review.

## R7. Preview within 10 seconds (FR-017, SC-002)

- **Decision**: the preview uses the same pipeline at the **preview** detail level:
  - coarse mesh of about 15–25 k degrees of freedom (about 5–8 k nodes; revised 2026-11-05 from the
    measurements below)
  - threads smeared into a cylinder
  - unit load cases only for the active contact points
- **Time budget**:

  | Step | Time |
  |---|---|
  | Geometry and mesh | ≤ 4 s |
  | Solve (PaStiX, 2 threads) | ≤ 3 s |
  | Post-processing and transfer | ≤ 2 s |
  | Safety margin | 1 s |

- **Measured on the main PC** (PaStiX, 2 threads, one contact point = 3 unit cases): 14 k DOF took 1.7 s
  (0.8 s for one case); 48 k DOF took 8.1 s, which alone breaks the budget. Previews with several active
  contact points need a still smaller mesh, or more threads when the heavy lane is idle.
- **How it behaves**:
  - Setting changes are debounced by 600 ms.
  - Results are cached by input hash (R14).
  - Previews run in their own lane and never wait behind a detailed run.
- **Accuracy shown to the owner**: every verification case is also solved at the preview level. The worst
  difference in key results is stored and shown beside each preview, for example "previews differ from detailed
  results by up to 12% on the verification cases".
- **Alternatives considered**:
  - A reduced-order or surrogate model: faster, but unverified physics.
  - Running the preview in the browser: forbidden by the spirit of Principle V, and too slow anyway.

## R8. Run time, memory and disk estimates (FR-009, FR-057)

- **Decision**: estimate from the expected degrees of freedom (DOF), counted from the detail level's
  element-size fields before meshing.
  - Fitted models: time ≈ a·DOF^b and peak memory ≈ c·DOF^d per solver build, plus disk ≈ e·DOF·(number of load
    cases).
  - Starting coefficients come from the R3 calibration runs. Every finished run adds its measured values and the
    fit is refreshed.
  - A run is refused when the expected memory is above 85% of the main PC's physical memory, with a suggestion
    to lower the detail level.
  - The worker warns when free disk space is less than twice the expected disk use.
  - A watchdog (psutil) stops the solver process cleanly if real memory passes 92%. The run is recorded as
    "failed: out of memory".
- **Measured on the main PC** (R3 table): peak memory grows as about DOF^1.35 for PARDISO and DOF^1.28 for
  PaStiX, and 758 k DOF needs 13.5–14.4 GB. Within the 85% limit of 31.8 GB (27 GB), the largest model is
  therefore about **1.2–1.3 M DOF**, not the 2 M first assumed. A uniform cube is close to the worst case for
  fill-in, so elongated, locally refined dental models should do somewhat better; T059 and T098 measure it.
  Time per unit case with PaStiX on 8 threads: about 70 s at 387 k and 150 s at 758 k DOF.
- **If detailed models do not fit or are too slow**, in order of cost: solve only the loaded contact points;
  refine only where reported regions need it (the mesh check decides the level, and region means and 95th
  percentiles converge much faster than peaks); a ccx build that factors once for all unit cases (R4); more
  memory in the main PC (64 GB would hold about 2.2 M DOF); a rented solver machine (R23).
- **Rationale**: these laws describe the cost of direct solvers well, and the fit improves as runs accumulate.
- **Alternatives considered**: fixed tables per template, which break when blocks are combined freely.

## R9. Queue and unattended running (FR-019, FR-056, SC-004)

- **Decision**: a separate **worker process** runs the jobs.
  - **Job storage**: jobs live in the studio database and lanes run them:

    | Lane | Runs at a time |
    |---|---|
    | Heavy | 1 |
    | Preview | 1 |

  - **Cores**: the heavy lane gets all physical cores minus 2 (`OMP_NUM_THREADS`; 8 on the main PC), so
    previews stay fast. Hyper-threads do not help the direct solvers. The calibration runs (R8) use the same
    count (`dentalsim.threads`).
  - **Restarts**: on start-up, any job left "running" is marked "interrupted" and put back at the front of the
    queue.
  - **Sleep**: while jobs exist, the worker asks Windows not to sleep (`SetThreadExecutionState`), so overnight
    studies are not stopped by sleep.
  - **Cancel**: cancelling ends the solver's process tree.
  - **Clean-up**: a failed or cancelled run keeps its log but never its partial fields.
- **Rationale**: one heavy run at a time is what the spec asks for. A database-backed queue survives restarts
  without Redis or Celery, which run poorly on Windows.
- **Alternatives considered**: Celery with Redis; Windows Task Scheduler per job.

## R10. Studio server, access control and the home network (FR-001, FR-002, SC-011)

- **Decision**: **FastAPI** served by **uvicorn**, on the main PC only. Every layer has to agree before a
  connection is accepted:
  - **Network filter**: the server listens on the main PC's address on the home network. A request from any
    address outside the configured home subnets (and loopback) is closed without a reply.
  - **Windows Firewall**: a rule allows the port only on the Private profile and only from the local subnet.
  - **Router**: no port forwarding (a checklist item in [quickstart.md](quickstart.md)). SC-011 is tested from a
    phone on mobile data.
  - **HTTPS**: a certificate is generated for the main PC's name and address at first start. The owner trusts it
    once on each computer.
  - **Password**: one owner password, stored as an Argon2id hash. Sign-in gives an HttpOnly, SameSite=Strict
    session cookie. Login attempts are rate-limited, and an idle session times out.
  - **Progress updates**: sent to the browser with Server-Sent Events.
- **Rationale**: people and devices share the home network, so being on the network is not enough on its own.
- **Alternatives considered**: no password, which the spec rejects; a VPN (Tailscale), which is out of scope
  since access from outside the network is not wanted.

## R11. Browser interface and 3D viewer

- **Decision**: **React 19 + TypeScript + Vite**, using:
  - the **Mantine** component library (forms, tables, notifications)
  - **TanStack Query** for server data
  - **three.js** through react-three-fiber for the 3D builder and result views
- **Result geometry**: sent as **glTF 2.0 binary (.glb)**, built from the outer surface of each part:
  - displacement stored as a **morph target**, so the movement animation and magnification are just the morph
    weight
  - result fields stored as custom vertex attributes and coloured by a shared colour-scale module
  - the colour-scale module is generated from the conventions block, so the studio, the figures and the website
    use identical scales (Principle VII)
- **Rationale**:
  - The same glTF result format and viewer code can be reused by the public website (feature 001), which is a
    static site.
  - The builder needs drag-and-place, collision highlighting and rich forms, which a component framework
    handles well.
- **Alternatives considered**:
  - trame (Kitware, Python-only, VTK remote rendering): strong for viewing, weaker for the builder and library
    forms, and not reusable on a static website.
  - Streamlit or NiceGUI: poor fit for 3D editing.
  - Electron: not needed, since the browser is the client by design.

## R12. Storage, search and backup (FR-031, FR-033, FR-034)

- **Decision**: a **data folder** outside git on the main PC (default `D:\DentalSimData`, configurable):
  - **`studio.db`** (SQLite in WAL mode) is the system of record. It holds projects, scenarios, runs, studies,
    jobs, the literature library with immutable versions, verification reports, publication records and notes.
  - **Search** uses SQLite **FTS5** over names, notes and tags, plus JSON queries over scenario settings to
    search by setting value.
  - **`store/`** holds content-addressed meshes, solver outputs and processed fields, compressed with zstd.
  - **`exports/`, `published-media/`, `logs/`, `backups/`** hold exports, published media, logs and backups.
  - **Backup**: `dentalsim backup` makes one archive from an online SQLite snapshot, the store, published media
    and a library export. Restore checks every file's hash.
- **Rationale**: a single user needs no database server, and SQLite's online backup is consistent while the
  studio is running. Large results stay out of git, as the constitution requires.
- **Alternatives considered**: PostgreSQL, which is overkill; keeping everything in plain files, which makes
  search and versioning harder.

## R13. Literature library and source checking (FR-012 to FR-016, FR-053 to FR-055)

- **Decision**:
  - **Versions**: entries live in the database. Approving or editing an entry creates an **immutable version**,
    and runs point to version ids.
  - **Bulk proposals**: YAML files in `content/library/proposals/`, written by Claude or by hand. They are
    imported with status "proposed" (format: [contracts/library-entry-format.md](contracts/library-entry-format.md)).
  - **Snapshots**: approved snapshots are exported to `content/library/snapshots/` so library history can also be
    reviewed in git.
  - **Source check**:
    - DOIs are checked with the Crossref REST API and PMIDs with NCBI E-utilities.
    - A source passes only when the first author's surname, the year and the title match. The title match is
    normalised similarity ≥ 0.90.
    - Requests use a generic client name and send no personal data.
    - Without internet the result is "not checked", and the entry cannot be approved.
- **Rationale**: version ids make runs traceable (FR-016, FR-023), and the check enforces FR-054.
- **Alternatives considered**: library files in git as the system of record. Every approval would then need a
  git commit made by the studio on a machine that is also used for development.

## R14. Input hashing, stale results and development builds (FR-058, Principle VI)

- **Decision**:
  - **Input hash**: every stored item is keyed by the SHA-256 of a canonical JSON input record. The record
    contains:
    - the fully resolved scenario (all block settings)
    - the library entry version ids and their content hashes
    - the detail level
    - the conventions version
    - the engine source hash: SHA-256 over the result-affecting engine source files (`conventions/`, `model/`,
      `blocks/`, `templates/`, `assembly/`, `geometry/`, `mesh/`, `physics/`, `solvers/`, `post/`, `pipeline.py`)
    - the solver build id
    - the gmsh version
  - **Engine version**: the package version plus the git commit, kept for provenance but not part of the hash,
    so interface, server or documentation commits never force a re-solve (Principle VI). A run made from
    uncommitted engine code is marked **"development build"** and can never be published.
  - **Out of date**: a stored item whose inputs no longer match the current engine, conventions or library is
    marked "out of date" and is never reused silently.
- **Rationale**: this implements "keyed by every input that affects them" literally, and stops results from code
  that was never committed reaching the website.

## R15. Verification suite (FR-035 to FR-037, SC-006)

- **Decision**:
  - **Case files**: cases live in `content/verification/cases/*.yaml`. Each file records:
    - the compared quantity
    - the expected value and its source
    - the tolerance
    - whether the case is required
  - **Recorded before first run**: git history proves the tolerance was set before the first run. The report also
    stores the hash of the case file. A later change needs a `tolerance_change` entry with its reason.
  - **Second solver**: textbook cases run on CalculiX and are cross-checked with scikit-fem.

**Textbook cases with exact answers** (bonded elastic, delivered in slice 1a):

| Case | Compared quantity | Expected value |
|---|---|---|
| Cantilever beam | Tip deflection | Timoshenko solution |
| Thick-walled cylinder under internal pressure (Lamé) | Radial and hoop stress | Lamé solution |
| NAFEMS LE10 thick plate | σyy at point D | −5.38 MPa |
| Constant-strain patch test | Exact constant strain | Exact |
| Two bonded bars of different stiffness in series | Interface continuity and stiffness | Series stiffness |
| Mesh convergence on the cylinder | Error drops as the mesh is refined | Expected rate for quadratic elements |

**Published dental implant studies to reproduce (candidates)**:

- The owner chooses 3 to 5 in Story 5, using the studio's source check.
- Criteria for a good candidate:
  - generic geometry fully described
  - linear elastic and bonded
  - loads fully stated
  - numbers or ratios given in tables
- Published numbers are often nodal peaks, so the compared quantities are trends and ratios between variants.
  Absolute values are compared only for region-type quantities the paper reports.

| Study | Why it fits | Identifier |
|---|---|---|
| Petrie & Williams 2005, *Clin Oral Implants Res* 16(4):486–494 | Generic factorial designs (diameter 3.5–6 mm, length 5.75–23.5 mm, taper 0–14°) reported as **crestal strain**. Up to a 3.5-fold reduction with diameter and a 1.65-fold reduction with length: ideal ratio targets. | PMID 16117775; DOI 10.1111/j.1600-0501.2005.01132.x (checked) |
| Baggi et al. 2008, *J Prosthet Dent* 100(6):422–431 | Diameter and length effects on cortical and cancellous stress. It uses commercial systems, so we compare only the generic diameter and length trends. | DOI 10.1016/S0022-3913(08)60259-0 (checked) |
| Himmlová et al. 2004, *J Prosthet Dent* 91(1):20–25 | Effect of diameter and length on stress at the implant neck. | To be checked |
| Sevimay et al. 2005, *J Prosthet Dent* 93(3):227–234 | Bone quality D1–D4 under one crown. | To be checked |
| Tada et al. 2003, *Int J Oral Maxillofac Implants* 18(3):357–368 | Implant design and bone quality, reported as **strain**. | To be checked |
| Kitamura et al. 2004, *Clin Oral Implants Res* 15(4):401–412 | Crestal bone geometry (resorption) and stress. | To be checked |

**Benchmarks for later physics** (planned in Story 9):

| Physics | Benchmark |
|---|---|
| Screw preload | Pretensioned bolt in an elastic sleeve against the hand calculation of joint stiffness |
| Periodontal ligament | Thin bonded layer in compression and shear (constrained-modulus solution), then a published tooth-mobility comparison |
| Contact | Hertz sphere on a flat; sliding block with Coulomb friction |
| Fatigue | Basquin and Goodman hand calculation, then a published screw or implant S-N comparison |

- **Mesh check (FR-037)**: solve at two detail levels. The largest relative change in key results is compared with
  the conventions limit and written to the verification note.

## R16. Exports (FR-038 to FR-044, SC-009)

- **Decision**:

  | Export | How it is made |
  |---|---|
  | Figures | **PyVista** (VTK) off-screen renders at 300 dpi (PNG), with the legend, scale, units, source line and notice drawn by the export template |
  | Films | Frames encoded with **ffmpeg** (imageio-ffmpeg binary): H.264, yuv420p, 1920×1080, 30 fps, `+faststart` |
  | Slide loops | The same encoder, 6–15 s, seamless loop |
  | Ready-made slide | **python-pptx**: a 16:9 slide with the clip embedded as a movie (with poster frame), title, lesson and source |
  | Run report | **Typst** (the `typst` Python package, no system dependencies) from a versioned template |
  | Numbers | `.xlsx` with **openpyxl**, plus `.csv` |

- **Regeneration**: every export is rebuilt only from stored results, the run record and a template version.
  "Identical in content" (FR-044) is tested by comparing:
  - numbers and text exactly
  - images by pixel difference below a fixed threshold, because GPU renderers can differ by a pixel between
    machines
- **Rationale**: one renderer is used for every pre-made visual. H.264 MP4 is what PowerPoint plays natively.
- **Alternatives considered**: WeasyPrint, which needs GTK on Windows; ReportLab, which takes more layout code;
  Matplotlib 3D, which looks worse.

## R17. Promo clips (FR-041, Story 8)

- **Decision**:
  - **Picture**: a vertical clip (1080×1920) is re-rendered from the stored results of a **published** scenario
    rather than cropped from the widescreen film.
  - **Voice**: **Kokoro-82M** text-to-speech, run locally. Its weights are under Apache-2.0, so commercial use is
    allowed and it costs nothing.
  - **Captions**: drawn onto the frames with Pillow and timed per sentence from the length of the generated
    audio.
  - **Encoding**: H.264 with AAC audio, 15–45 s, ending with an end card and the teaching-only notice.
  - **Records**: the script is approved by the owner and stored with the clip, along with the voice model's name,
    version and licence.
- **Rationale**: it is free, private and commercially licensed, and nothing leaves the PC.
- **Alternatives considered**:
  - Paid cloud voices (ElevenLabs, Azure Neural TTS): good quality, but a recurring cost. They remain an
    option behind the same voice interface.
  - Piper voices: the licence differs from voice to voice.

## R18. Interactive page data (FR-063, SC-015)

- **Decision**:

  | Kind of control | How values are produced |
  |---|---|
  | Bite angle and bite force | Exact superposition (R4), no interpolation |
  | Discrete choices (contact point, jaw, bone type) | Each value is a stored, solved case |
  | Continuous controls that cannot be superposed | Linear interpolation between neighbouring solved cases |

  - **Interpolation error**: measured on at least one solved case held out from the fit and stated in the
    verification note.
  - **No extrapolation**: control ranges are clamped to the solved range.
  - **Package contents**: key numbers per solved case, the glTF fields, and the method with its error.
  - **Built-in check**: "solved cases equal stored runs" is verified automatically during publishing.

## R19. Published scenario package (FR-046, FR-047)

- **Decision**:
  - **In git**: `content/scenarios/<slug>/v<N>/` holds `package.json` (format:
    [contracts/published-package.md](contracts/published-package.md)) plus small assets such as stills and
    lightweight `.glb` files.
  - **Outside git**: large media (films, large `.glb`) go to `published-media/`. Each file is named by its
    SHA-256 and listed in the package with its size.
  - **Versions**: published versions are never edited. Republishing writes v\<N+1\>, and unpublishing writes a
    record, not a deletion.
- **Rationale**: git stays small (constitution, Scope) and every published version can be audited. Feature 001
  decides how media is hosted for the public site.
- **Alternatives considered**: Git LFS, which has a limited free quota and only moves the size problem to GitHub.

## R20. Testing and the merge gate

- **Decision**:
  - **Python**: **pytest** tests, grouped by marker:

    | Marker | Covers |
    |---|---|
    | `unit` | Single functions and modules |
    | `integration` | Pipeline and API, at small sizes |
    | `benchmark` | Needs the solver: textbook and published cases |
    | `regression` | Reference numbers of every published scenario |

  - **Interface**: **Vitest** for interface logic, plus a few **Playwright** smoke tests of key flows (sign in,
    build from a template, run a preview, compare).
  - **Merge gate**: `dentalsim check` runs the full gate before any merge (constitution, Workflow).
- **Rationale**: regression-based testing, as the constitution allows, with mandatory benchmarks for every solver
  capability.

## R21. Delivery order and the scope of Story 9

- **Decision**:
  - **This plan delivers** Stories 1–8 on bonded-elastic physics (slices 1a and 1b), together with the full
    physics-capability framework: gating, benchmark status, locking and unlocking (FR-011a, FR-062).
  - **Story 9 physics are each planned and tasked as their own increment**, after the published-study
    reproductions pass, because each needs its own benchmark research and library entries. In order:
    1. screw preload
    2. periodontal ligament
    3. contact
    4. fatigue
  - **Locked results until then**: those blocks and result types appear in the catalogue as "awaiting
    benchmark", so locked results display correctly (Story 1, scenarios 7–8).
- **Rationale**: keeps this feature's task list buildable and shippable, as the spec's Assumptions allow.

## R22. Platform

- **Decision**:
  - Both PCs run Windows 11.
  - **Main PC**: runs the server, the worker and the solver.
  - **Laptop**: a browser client, and the development machine. Everything also runs on the laptop at small sizes
    for development.
  - **The 4060 Ti**: used for rendering films and figures. The direct solver runs on the CPU.
- **Rationale**: matches the spec's Assumptions and the owner's hardware. A GPU solver is not needed to meet the
  30-minute and 10-hour targets.

## R23. Rented solver machine (FR-064, constitution 2.1.0 Scope: Compute)

- **Decision**: when a run is too large or too slow for the main PC, the owner may rent a more powerful
  machine (a cloud virtual machine or a remote workstation, Windows or Linux) as a **solver machine**. Only the
  solver step moves there; everything else stays on the main PC.
  - **What moves**: the worker writes the solver input (`.inp`) as usual, copies it to the rented machine over
    SSH (OpenSSH, built into Windows 11 and Linux), runs ccx there with the same thread variables, streams its
    output back for progress, copies the `.frd`, `.dat` and log back, and deletes the remote work folder. Meshing,
    post-processing, the convergence checks and storage stay on the main PC.
  - **Access**: key authentication only (the key stays in the owner's user profile, never in the repository or
    the database); the rented machine opens no other port; no studio service runs there.
  - **Solver build**: the rented machine's ccx is identified like any other (`ccx-<version>-<solver>-sha256:…`,
    the hash computed on that machine), so its results are keyed separately (R14). Every capability a run needs
    must have passed its benchmarks with that build (`dentalsim verify --host`) before runs go there; the gate
    treats an unverified build as "awaiting benchmark" (FR-011a). It gets its own estimate coefficients (R8).
    - Windows: copy `ccx_static.exe` (the same file and hash as the main PC's).
    - Linux: build ccx 2.23 with PaStiX from source (Ubuntu's package is 2.21); a different build id.
  - **Failure**: a lost connection ends the run as failed, storing nothing partial (FR-022); the remote process
    tree is ended; the owner can re-queue it.
  - **Choosing it**: per run or study, in the estimate dialog; the default stays the main PC. Previews always
    run on the main PC (they must answer within 10 s).
- **Rationale**: the measurements in R8 put the main PC's ceiling at about 1.2 M DOF. Renting a machine with
  128–256 GB for an overnight batch is cheap compared with buying one, and keeping only the solver step remote
  keeps the studio on the home network (FR-001) and every result on the owner's PCs.
- **Alternatives considered**:
  - Running the whole studio on the rented machine: breaks FR-001 (home network only) and puts the database
    outside the owner's PCs.
  - A managed cloud batch service: more setup and lock-in for an occasional need.
  - A remote desktop session used by hand: not reproducible, and nothing records which build solved the run.
