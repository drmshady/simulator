<!--
Sync Impact Report
==================
Version change: 2.0.0 -> 2.1.0 (MINOR: compute may include a rented solver machine; materially expanded
guidance in Principle V and Scope)

Modified principles:
  V.   Solve Offline, Deliver Instantly - clarified: solves are run by the owner's simulator, on the owner's PCs
                                          or on a solver machine the owner rents and controls; the website
                                          still never solves

Modified sections:
  - Scope and Delivery Constraints: Products and Compute allow a rented solver machine for runs too large or
    too slow for the main PC, with the rules it must follow (inputs only, encrypted key-authenticated access,
    same open-source solver verified on that machine, nothing kept there)

Added sections: none
Removed sections: none

Templates:
  ✅ .specify/templates/plan-template.md   - Constitution Check reads this file; no edit needed
  ✅ .specify/templates/spec-template.md   - no mandatory section change
  ✅ .specify/templates/tasks-template.md  - no change
  ✅ specs/002-simulation-studio/          - spec FR-002, FR-057 revised and FR-064 added; plan, research R23,
                                              data model and tasks (Phase 14) updated on 2026-11-05

Deferred TODOs: none.

Previous report (2.0.0)
-----------------------
Version change: 1.0.0 -> 2.0.0 (MAJOR: Principle IV redefined; project restarted from scratch)

Modified principles:
  I.   Teaching, Not Prediction       - adds: imported geometry is never a patient's scan
  II.  Verified Before Published      - checks now run against the engine benchmark suite and cited
                                        published studies (no longer the old project's reference numbers)
  IV.  One Shared Physics Convention  - REDEFINED: conventions are defined once for the new engine in a
                                        project conventions document, not inherited from the old project
  VI.  Reproducible Pipeline          - outputs regenerate through the simulator or its command-line form

Added principles:
  VIII. Literature-Sourced Inputs

Modified sections:
  - Scope and Delivery Constraints: two products (local simulator, public website) in one repository;
    simulator built first; open-source stack; compute on the owner's PCs; old project is reference only
  - Development Workflow and Quality Gates: engine verification gate (textbook benchmarks + 3-5 published
    studies) before the first scenario is published

Removed sections: none

Templates:
  ✅ .specify/templates/plan-template.md   - Constitution Check reads this file; no edit needed
  ✅ .specify/templates/spec-template.md   - no mandatory section change
  ✅ .specify/templates/tasks-template.md  - testing and publication-gate notes still match
  ⚠ specs/001-platform-mvp/spec.md        - Assumptions say launch content reuses the old project's solved
                                              results; conflicts with "start from scratch". Revise before
                                              planning 001.

Deferred TODOs: none.
-->

# Dental Simulator Constitution

Dental Simulator has two products in one repository: a local simulator, used only by the project owner to
build, run, check and publish finite element simulations of dental prostheses, and a free, public,
English-language website where dental students, residents and dentists anywhere learn from the published
results. The project is built from scratch. The earlier FEA implant teaching module
(`fea-implant-teaching/`) only illustrates the goal and serves as a source of benchmark cases.

## Core Principles

### I. Teaching, Not Prediction (NON-NEGOTIABLE)

- Every published scenario page, film, slide animation and promo video MUST state that the results are for
  teaching and are not a patient-specific prediction.
- The platform MUST NOT accept patient data or offer patient-specific simulation, in the free tier or in any
  future paid tier. Imported geometry MUST NEVER be a patient's own scan; only generated shapes or
  anonymised, published reference models are allowed.
- Values MUST NOT be presented as clinical thresholds. Reference windows (Frost strain windows, material
  strength) MUST be shown with their source and labelled as teaching references.

Rationale: credibility with clinicians, and patient-specific simulation would turn the platform into
regulated medical-device software (SFDA, FDA).

### II. Verified Before Published

A scenario MUST NOT be published until all of the following hold:

- The automated test suite passes, including the engine benchmark suite.
- Results are compared with cited published studies or benchmark cases where comparable ones exist, and
  any difference is explained in writing.
- Mesh resolution is justified, either by a convergence study for the scenario or by a documented carry-over
  from a verified model at the same resolution.
- Solver residuals are recorded.
- The project owner has reviewed the scenario and its teaching message and signed it off. One clinical
  reviewer is sufficient.

Reported values MUST be region means or 95th percentiles. Singular corner peaks MUST NOT be reported, because
they depend on the mesh. Each scenario MUST keep a verification note alongside its results.

### III. Transparent Assumptions

- Each scenario MUST document its geometry and its source, material properties with references, loads
  (magnitude, direction, contact point), boundary conditions, interface assumptions (bonded, contact,
  preload), model detail level and known limits, including the likely direction of error.
- A public "Assumptions and Limits" page MUST link to the assumptions of every published scenario.
- Literature values MUST be cited.

Rationale: experts will judge the platform by its assumptions; stating them first is the defence.

### IV. One Shared Physics Convention

- The project MUST define one convention set in a single conventions document in the repository, before
  the first scenario is built: units (mm, N, MPa, microstrain), coordinate frame, load-angle sign, region
  definitions for reported values, the Frost-window colour scale, and how material strength references are
  chosen from the literature library.
- Every scenario, prosthesis type and output MUST use that convention set, so every scenario can be compared
  with every other. New prosthesis types MUST extend it, not replace it.
- Changing a convention is a constitution-level decision and MUST be followed by re-verification of every
  published scenario that depends on it.

### V. Solve Offline, Deliver Instantly

- Finite element solves MUST be run by the local simulator, never by the public website: on the owner's own
  PCs, or on a solver machine the owner rents and controls (see Scope, Compute). The website MUST only read
  pre-computed, published results. No solve may run in a visitor's browser or on a server at request time.
- Website interactive controls MUST respond without perceptible delay on a mid-range phone.
- One website codebase MUST serve both layouts: a textbook layout with a contents list on laptops and a card
  layout on phones, each with a "find by situation" entry.
- The free tier MUST work without sign-up. The structure MUST allow adding accounts and payments later
  without rebuilding the content library.

### VI. Reproducible Pipeline

- Every published output (figure, film, slide animation, interactive page data, promo video, run report)
  MUST be regenerable from version-controlled code, the approved literature library and stored results,
  through the simulator or its command-line equivalent. Outputs MUST NOT be edited by hand.
- Cached meshes and results MUST be keyed by every input that affects them, so a stale cache is never reused
  silently.
- Solved results MUST be stored so outputs rebuild without re-solving. Re-solving is required only when
  geometry, materials or loads change.

### VII. Honest Visuals and Communication

- Every figure and every film frame MUST be inspected before release, including on a phone-sized screen.
- Panels shown side by side MUST share one colour scale and one deformation scale. Any deformation
  magnification MUST be stated on screen.
- Promo videos MUST be cut from real output of published scenarios, MUST NOT exaggerate effects or imply
  clinical guarantees, and MUST carry the teaching-only statement or link to it.
- The library MUST be brand-neutral: no implant or material brand names and no sponsored content. The single
  exception is a scenario whose stated purpose is comparing commercial designs; such a scenario MUST apply
  identical loads, bone and conventions to every product, cite each geometry source, and disclose any
  relationship with the manufacturers.

### VIII. Literature-Sourced Inputs

- Every material property, bone parameter, load value and strength reference used by a simulation MUST
  come from the approved literature library, with its source (authors, year, DOI or PMID).
- The library MUST record the published range for each parameter, not only one value. The simulator MUST
  show that range and its sources when the owner chooses a value.
- No library entry may be used in a simulation until the project owner has approved it. Entries proposed
  from searches and entries from papers the owner supplies follow the same approval.
- A run MUST record exactly which library entries (and versions) it used.

Rationale: published values vary widely; showing the range and the source is what makes results defensible.

## Scope and Delivery Constraints

- Audience: dental students, residents and dentists worldwide. Language: English.
- Products: (1) the local simulator, used only by the owner, running on the owner's own computers and
  reachable only on the home network, never from the internet (a rented solver machine, below, only solves
  for it and is never reachable as the simulator); (2) the public website, a library browsed
  freely, not a sequenced course. Both live in one repository with a shared engine and a shared content
  format.
- Build order: the simulator first; the website after the first scenarios are verified and published.
- Prosthesis order: implant prostheses (single crown first), then tooth-supported, then removable. The
  simulator MUST be structured so new prosthesis types can be added without rebuilding it. The unit of work
  is one scenario, and each scenario is publishable on its own.
- Year-one target (by 2027-11-05): 30 published scenarios, 10 per branch, and at least 50 website users,
  measured with privacy-friendly visitor statistics. The free tier MUST NOT collect personal data.
- Simulation stack: free, open-source tools only, so results can be published and monetised without
  licence limits. The specific solvers and libraries are chosen in each feature plan. New physics (contact,
  periodontal ligament, screw preload, fatigue) MUST pass a benchmark with a known answer before any scenario
  uses it.
- Compute: the owner's stronger PC (RTX 4060 Ti, about 32 GB memory) is the main simulation machine;
  overnight runs are acceptable. When a run is too large or too slow for it, the owner MAY rent a more
  powerful machine as a solver machine. A rented solver machine:
  - receives only generated solver inputs and returns solver results (the simulator holds no patient data);
  - is reached only by the simulator, over an encrypted, key-authenticated connection, and offers no other
    service;
  - runs the same free, open-source solver, identified as its own solver build, which MUST pass the engine
    benchmark suite on that machine before any of its results are used;
  - keeps no inputs or results after a run has been returned.
  The simulator, its database and every stored result stay on the owner's PCs. Large solved results MUST NOT
  bloat normal version-control history.
- Teaching films and slide animations: H.264 MP4 that presentation software plays directly.
- Promo videos: vertical 9:16, 15 to 45 seconds, for YouTube Shorts, Instagram, Facebook and TikTok; a
  generated voice whose licence permits commercial use; captions burned in; widescreen cuts for longer pieces
  when useful.
- The website free tier MUST be deployable as a static site.
- The earlier `fea-implant-teaching/` project is reference material only: its code, parameters and results
  MUST NOT constrain the new design, and its verified numbers MAY serve as benchmark cases.

## Development Workflow and Quality Gates

- Features follow the Spec Kit sequence: specify, clarify when needed, plan, tasks, implement. Every plan's
  Constitution Check MUST evaluate Principles I to VIII, and any violation MUST be justified in the plan's
  Complexity Tracking table.
- Testing is regression-based, not strictly test-first. Tests MUST cover the conventions, the engine
  benchmark suite, the reference numbers of every published scenario, and published data reproducing solver
  results. Each new solver capability MUST have at least one benchmark test with an analytic or published
  answer. The full test suite MUST pass before any change is merged.
- Engine verification gate: before the first scenario is published, the engine MUST pass textbook benchmark
  problems with exact answers and reproduce 3 to 5 published dental implant FEA studies within a stated
  tolerance.
- Scenario publication gate: verification note, assumptions note, owner sign-off, frame-by-frame review,
  tests passing, teaching-only notice present, all inputs from approved library entries. A scenario missing
  any item stays unpublished.
- Commit per scenario or per logical group of changes.

## Governance

- This constitution supersedes other project practices. Where project notes (for example `CLAUDE.md`
  files) conflict with it, the constitution wins and the notes are updated.
- Amendments are proposed in writing, approved by the project owner, and recorded with a version bump and a
  Sync Impact Report at the top of this file.
- Versioning: MAJOR for removing or redefining a principle; MINOR for a new principle or section or
  materially expanded guidance; PATCH for wording, clarification or the platform name.
- Compliance is checked in every plan's Constitution Check, at every scenario publication, and at least once
  a quarter against the year-one targets.

**Version**: 2.1.0 | **Ratified**: 2026-11-05 | **Last Amended**: 2026-11-05
