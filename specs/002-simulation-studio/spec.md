# Feature Specification: Simulation Studio

**Feature Branch**: `002-simulation-studio`
**Created**: 2026-11-05
**Status**: Draft
**Input**: User description: "Dental Simulator local simulation studio: a tool used only by the project
owner, running on the owner's strong PC and opened in a browser from either home computer (never on the
internet), to set up, run, check and publish finite element simulations of dental prostheses, built from
scratch with open-source tools. First version: single implant crown, structured so other prosthesis types plug
in later. [...] Built before the website."
**Scope change (2026-11-05)**: the owner widened Story 1 from the single implant crown to a building-block
builder covering implant, tooth-supported and removable prostheses (option C); results unlock per kind of
physics as each passes its benchmark.
**Revision (2026-11-05, after the independent spec check)**: Story 1 is delivered in slices (single implant
crown first, then multi-implant fixed prostheses; tooth and denture blocks arrive with their physics in
Story 9). Added: the conventions document, screw preload as gated physics, interactive page data, the full
published scenario package, and requirements for the edge cases (FR-050 to FR-063).
**Revision (2026-11-05, after /speckit-analyze)**: Story 9's physics moved to later features; "detailed" made
measurable before the mesh check exists; convergence defined for the direct solver; re-verification and review
flags given a clearing rule; staleness keyed by result-affecting engine code only; load patterns, load-direction
settings and verification-case inputs clarified (FR-008, FR-017, FR-018, FR-023, FR-035, FR-051, FR-052,
FR-058, FR-062, SC-001a).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Build any scenario from building blocks and run it (Priority: P1)

The owner, a prosthodontist, opens the studio in the browser on either home computer and builds a scenario by
assembling building blocks on a jaw segment. Examples: two implants in the lower molar region carrying a
three-unit bridge with a distal cantilever; a natural premolar with its periodontal ligament next to an implant
crown; a lower complete denture resting on the mucosa; an overdenture on two implants with attachments. Common
prosthesis types are offered as ready-made templates that the owner can change block by block. Every block's
settings fall into five groups (patient bone, implant, prosthesis, bite and load, model detail). Material,
bone, load and strength values come from the approved literature library, with published ranges and sources
shown; sizes and positions are the owner's design choices. While the owner edits, a preview
updates within seconds. A detailed run then shows bone strain in Frost-window colours with crestal values for
each implant and tooth, stress in every component as a percentage of its material's strength, and a magnified
movement animation.

Every kind of physics must first pass a known-answer benchmark (Constitution, Scope and Workflow). This
includes bonded elastic, the first one: its textbook benchmarks are part of this story and must pass before
the first preview or run. Natural teeth need the periodontal ligament model; dentures on mucosa, attachments
and immediately loaded implants need contact; screw results need screw preload. The studio marks each block
"ready" or "awaiting benchmark". A scenario with a block awaiting its benchmark can be built and saved, and
its results unlock when the benchmark passes (Story 9).

**Delivery slices** (owner decision 2026-11-05, option A): the builder is block-based from the start. Blocks
are delivered in this order, and each slice is usable on its own:

- **Slice 1a (first working version)**: the conventions document (FR-050), the bonded-elastic textbook
  benchmarks, and the single implant crown end to end: jaw segment, implant, abutment and crown blocks; the
  "single crown (implant)" template; preview, detailed run and results.
- **Slice 1b**: multi-implant fixed prostheses: pontic, splinting connector and cantilever extension blocks;
  the splinted crowns, implant bridge, cantilever bridge and full-arch fixed templates.
- **With Story 9**: natural tooth, mucosa, bar, attachment, denture base, artificial teeth, clasp, rest and
  major connector blocks and their templates, each delivered together with the physics it needs.

**Why this priority**: the scenario builder is the heart of the studio. With only this story, the owner can
build and run every implant-supported fixed scenario in the year-one plan.

**Independent Test**: for slice 1a, confirm a preview or run is refused while the bonded-elastic benchmarks
have not passed; then build a single implant crown from its template, change it, run it in detail and confirm
its results. For slice 1b, build a cantilever bridge on two implants, save it, run it in detail and confirm
its results.

**Acceptance Scenarios**:

1. **Given** the builder, **When** the owner assembles a scenario, **Then** every block delivered so far
   (slice 1a: jaw segment, implant, abutment, crown; slice 1b adds pontic, splinting connector, cantilever
   extension) can be added, positioned and angled on a jaw segment; library settings show approved ranges and
   sources, and design settings show their allowed limits (FR-052).
2. **Given** a prosthesis-type template, **When** the owner opens it, **Then** a complete scenario is created
   that can be changed block by block and saved as a new template.
3. **Given** an assembly that cannot exist (overlapping parts, a pontic with no support, an implant entering
   the nerve canal, the sinus or a neighbouring root, an implant longer than the available bone), **When** the
   owner tries to run it, **Then** the run is refused with a plain explanation and the location of the
   problem.
4. **Given** a scenario whose blocks are all ready, **When** any setting changes, **Then** the preview result
   updates within 10 seconds and is labelled "preview".
5. **Given** a scenario whose blocks are all ready, **When** the owner starts a detailed run, **Then** the
   expected run time and memory are shown before it starts, progress and remaining time are shown while it
   runs, and a single implant crown finishes within 30 minutes on the main simulation PC.
6. **Given** a finished run, **When** the owner opens it, **Then** the bone strain map (Frost colours, legend
   with source, crestal value per implant and tooth), component stresses (% of strength reference with its
   source, mean and 95th percentile) and the movement animation (magnification stated) are shown, each with
   the teaching-only notice.
7. **Given** a scenario containing a block awaiting its benchmark (including any block before the
   bonded-elastic textbook benchmarks pass), **When** the owner asks for a preview or a detailed run,
   **Then** it is refused and the benchmark it waits for is named.
8. **Given** a finished run and the screw preload benchmark has not passed, **When** the owner opens the run,
   **Then** the screw is listed as "not reported: needs screw preload" and all other results are shown.

---

### User Story 2 - Build and approve the literature library (Priority: P2)

The owner needs defensible inputs. Claude proposes entries from literature searches, for example "cancellous
bone elastic modulus, D4: 0.2 to 1.4 GPa, 6 studies", each with authors, year, title and DOI or PMID. The
owner adds a paper they trust and its values. The owner reviews every proposed entry, approves or rejects it,
and can edit it; an edited approved entry keeps its earlier version so past runs stay traceable.

**Why this priority**: the constitution (Principle VIII) forbids any input without an approved source. A
minimal seed is enough for Story 1; the full library makes the studio trustworthy.

**Independent Test**: add one proposed entry and one owner-supplied entry, approve one and reject the other,
and confirm only the approved one can be chosen in a scenario and that both show their sources.

**Acceptance Scenarios**:

1. **Given** a proposed entry, **When** the owner has not approved it, **Then** it cannot be selected in any
   scenario.
2. **Given** an approved entry with several sources, **When** the owner picks a value for a scenario,
   **Then** the published range, the typical value and every source are visible, and the owner can choose a
   value within the range or a specific source's value.
3. **Given** an approved entry used by past runs, **When** the owner edits it, **Then** a new version is
   created and the past runs still show the version they used.
4. **Given** a proposed entry with a source whose DOI or PMID does not resolve to the cited paper, **When**
   the owner tries to approve it, **Then** approval is refused until the source is corrected.

---

### User Story 3 - Organise and compare runs (Priority: P3)

The owner keeps a project "Implant length study". They duplicate a scenario, change only the implant length
from 10 mm to 8 mm, run it, and open both runs side by side. The studio uses one colour scale and one
deformation scale for both, lists exactly which settings differ, and shows the difference in the key numbers.
Later they find the project by searching for a tag or a note.

**Why this priority**: teaching is mostly comparison ("short vs long implant"), but single runs already have
value.

**Independent Test**: create a project, duplicate a scenario changing one setting, run both, compare them, and
find them again by tag and by note text.

**Acceptance Scenarios**:

1. **Given** a scenario, **When** the owner chooses "duplicate and change", **Then** the copy keeps a link to
   the original and the comparison view lists the changed settings.
2. **Given** two or more runs, **When** they are compared, **Then** all panels share one colour scale and one
   deformation scale and the differences in key numbers are shown.
3. **Given** runs with notes and tags, **When** the owner searches by text, tag or setting value, **Then** the
   matching projects, scenarios and runs are listed.
4. **Given** a finished run, **When** the owner reopens it later, **Then** results appear without re-running.

---

### User Story 4 - Queue overnight studies and sensitivity checks (Priority: P4)

Before leaving the main PC overnight, the owner queues a study: the same scenario with six implant lengths
and three bone densities (18 runs). They also press the sensitivity button on "cancellous bone modulus",
which adds minimum, typical and maximum runs from the library range. In the morning, every run has finished
one after another, and a summary shows how much each result changed across the range.

**Why this priority**: studies and sensitivity make results defensible and productive, but they build on
single runs.

**Independent Test**: queue a study of at least 6 runs and one sensitivity check, close the browser, and
confirm the next day that all runs completed, failures (if any) are explained, and the summary appears.

**Acceptance Scenarios**:

1. **Given** a study of many runs, **When** it is queued, **Then** runs execute unattended one heavy run at a
   time, continue after the browser is closed, and can be cancelled.
2. **Given** a library parameter with a published range, **When** the owner presses the sensitivity button,
   **Then** minimum, typical and maximum runs are created and the spread of each key result is reported.
3. **Given** a run that fails, **When** the study continues, **Then** the failure and its reason are recorded,
   no partial result is shown as complete, and the remaining runs still execute.

---

### User Story 5 - Prove the engine is right (Priority: P5)

Before publishing anything, the owner runs the verification suite: textbook problems with exact answers (for
example a loaded beam and a pressurised cylinder) and reproductions of 3 to 5 published dental implant finite
element studies, each compared with the published numbers within a stated tolerance. The studio reports pass
or fail per case and stores the report.

**Why this priority**: required by the constitution before the first publication. The bonded-elastic textbook
benchmarks are delivered with Story 1 and gate its first run; the published-study reproductions here gate
publishing, not building and exploring.

**Independent Test**: run the suite and confirm each case reports the expected value, the computed value, the
tolerance and pass or fail, and that publishing stays locked while any required case fails.

**Acceptance Scenarios**:

1. **Given** the verification suite, **When** it runs, **Then** every textbook case and every published-study
   case shows expected value, computed value, tolerance and pass or fail, and each tolerance is the one
   recorded before that case was first run.
2. **Given** a failing required case, **When** the owner tries to publish any scenario, **Then** publishing is
   refused and the failing case is named.
3. **Given** a scenario, **When** the owner requests a mesh check, **Then** the scenario is solved at two
   detail levels and the change in key results is recorded in its verification note, marked pass or fail
   against the mesh-check limit in the conventions document.

---

### User Story 6 - Export material for lectures and records (Priority: P6)

For a lecture, the owner exports the bone-strain comparison as a print-quality figure, a widescreen teaching
film, and a slide-ready animation that plays directly in their presentation software, optionally as a
ready-made slide with the clip, a title, the one-line lesson and the source. For their records they export a
PDF run report (settings, literature sources, results, limits, verification status) and the raw numbers as a
spreadsheet file.

**Why this priority**: exports turn results into teaching material, but they depend on finished runs.

**Independent Test**: from one finished run, produce each export type and open each in the program it is
meant for.

**Acceptance Scenarios**:

1. **Given** a finished run, **When** the owner exports a figure, **Then** it is print quality and includes the
   legend, scale, units, source line and teaching-only notice.
2. **Given** a finished run or comparison, **When** the owner exports a slide animation, **Then** it is a short
   widescreen loop that plays in the owner's presentation software without conversion; the optional
   ready-made slide contains the clip, title, lesson and source.
3. **Given** a finished run, **When** the owner exports a run report, **Then** it lists every setting, every
   literature entry and version used, the key results, the limits and the verification status.
4. **Given** any export, **When** it is regenerated later from the stored run, **Then** it is identical in
   content (no hand-made edits are needed or possible).

---

### User Story 7 - Publish a scenario to the website (Priority: P7)

The owner decides a verified scenario is ready for the public. They fill in its one-sentence teaching message,
assumptions note and references, then mark it for publication. The studio checks the publication checklist and
either refuses, naming what is missing, or produces the published scenario package that the website will read
once it exists.

**Why this priority**: the website is built after the studio, so publishing is needed only once the first
scenarios are verified.

**Independent Test**: try to publish a scenario with one checklist item missing (refused, item named), complete
it, publish, and confirm the package contains everything the website spec requires.

**Acceptance Scenarios**:

1. **Given** a scenario with an incomplete checklist, **When** the owner publishes, **Then** publication is
   refused and every missing item is named.
2. **Given** a complete checklist, **When** the owner publishes, **Then** a published scenario package is
   produced with every field listed in FR-046.
3. **Given** a published scenario, **When** the owner unpublishes or republishes it, **Then** the change is
   recorded and earlier published versions are kept.
4. **Given** a scenario being published with an interactive view, **When** the owner produces its interactive
   page data, **Then** the data records its control ranges and solved cases, every solved case equals the
   stored run's numbers, and no value lies outside the solved ranges.

---

### User Story 8 - Make a promo clip (Priority: P8)

The owner wants a 30-second clip for social media. From a published scenario's film, the studio produces a
vertical clip with burned-in captions, a generated voice reading the owner-approved script, the teaching-only
notice and an end card with the platform name and link.

**Why this priority**: promotion matters after content exists.

**Independent Test**: create a promo clip from a published scenario and confirm format, length, captions,
voice, notice and end card.

**Acceptance Scenarios**:

1. **Given** a published scenario, **When** the owner creates a promo clip with an approved script, **Then** a
   vertical clip of 15 to 45 seconds is produced with captions, generated voice, the teaching-only notice and
   an end card.
2. **Given** a scenario that is not published, **When** the owner tries to create a promo clip, **Then** it is
   refused (promo clips come only from published scenarios).

---

### User Story 9 - Unlock new physics: screw preload, ligament, contact and fatigue (Priority: P9, phased)

Each new kind of physics is added, benchmarked and then unlocked, together with the blocks and templates that
need it: screw preload (stress in the retention screw), the periodontal ligament (natural teeth and
tooth-supported prostheses), contact (dentures on mucosa, bars, attachments, clasps, and micromotion of
immediately loaded implants against the literature tolerance range), and fatigue (estimated life of the screw
and implant under chewing cycles). When a benchmark passes, every block and result type that needs that
physics turns "ready", and saved scenarios that use it can be previewed and run.

**Scope in this feature** (decided at planning, research R21): this feature delivers only the lock and unlock
framework for Story 9 (Acceptance 4, FR-011a, FR-059, FR-062). Screw preload, the periodontal ligament, contact
and fatigue, with their blocks, templates, library entries and Acceptance scenarios 1, 2, 3 and 5, are each
delivered as their own later feature, in that order.

**Why this priority**: tooth-supported and removable scenarios depend on it, but each kind of physics must
first pass a known-answer benchmark (Constitution, Scope); it follows the bonded-elastic work in Stories 1 to 8.

**Independent Test**: for each kind of physics, confirm its blocks and result types stay locked until its
benchmark passes, then unlock with the benchmark report linked.

**Acceptance Scenarios**:

1. **Given** the ligament benchmark has passed, **When** the owner runs a scenario with natural teeth,
   **Then** tooth movement and the bone strain around each root are shown.
2. **Given** the contact benchmark has passed, **When** the owner runs a complete denture or an overdenture,
   **Then** pressure on the mucosa, denture movement and attachment loads are shown, and immediately loaded
   implants show micromotion against the literature range.
3. **Given** the fatigue benchmark has passed, **When** a run finishes with a chewing-cycle load, **Then** an
   estimated fatigue life for the screw and implant is shown with its sources and limits.
4. **Given** any of these benchmarks has not passed, **When** the owner opens a scenario that needs it,
   **Then** the locked results and the missing benchmark are named.
5. **Given** the screw preload benchmark has passed, **When** a run finishes, **Then** screw stress is shown
   as a percentage of its strength reference, with the preload included and its source named.

---

### Edge Cases

- The main PC is switched off or asleep while a study is queued: on restart, runs that were in progress are
  marked "interrupted" and put back in the queue; nothing is reported as complete that did not finish.
- A run needs more memory than the PC has: it is refused before starting when its estimate is too high; if
  memory still runs out mid-run it stops cleanly. Both give a plain message suggesting a lower detail level.
- Two detailed runs are started at once: the second waits in the queue; heavy runs never run in parallel.
- A literature entry is rejected after past runs used it: those runs are flagged "uses a rejected entry" and
  cannot be published until re-run.
- A library setting value outside the published range is requested: it is allowed only with an explicit
  "outside published range" mark that blocks publication.
- A convention is changed: every stored run and published scenario that depends on it is flagged for
  re-verification.
- Engine code that affects results is updated after runs were stored: those runs are flagged "out of date" and
  must be re-run before they can be newly published. Interface, server or documentation changes do not flag
  them.
- The laptop loses its connection to the main PC mid-run: the run continues on the main PC and the laptop
  shows its status again on reconnection.
- Someone outside the home network tries to open the studio: the connection is refused.
- Disk space runs low: the owner is warned before a run or study starts, with the space it is expected to need.
- A scenario is changed after being published: the published version stays unchanged until the owner
  republishes.
- A block is moved so that it collides with another block: the collision is shown at once while building,
  not only when a run is started.
- A template is opened for a jaw region where it does not fit (for example a full-arch template on a short
  segment): the owner is told what must change instead of getting a broken assembly.
- A saved scenario uses a block whose physics later fails a re-run of its benchmark: the block returns to
  "awaiting benchmark" and published scenarios that depend on it are flagged for review.

## Requirements *(mandatory)*

### Functional Requirements

**Access**

- **FR-001**: The studio MUST be usable only by the owner, from the owner's computers on the home network,
  MUST refuse connections from outside the home network, and MUST ask for the owner's password before it
  opens (other people and devices share the home network).
- **FR-002**: The studio MUST run on the owner's main simulation PC and MUST be usable from that PC and from
  the owner's laptop through a web browser. Its simulations run on the main PC or, when the owner chooses, on a
  rented solver machine (FR-064).

**Conventions**

- **FR-050**: Before the first scenario is built, the project MUST have one conventions document
  (Constitution, Principle IV) that defines: units (mm, N, MPa, microstrain); the coordinate frame; the
  load-angle sign; the regions used for reported values, including the crestal region and "the worse side";
  the Frost-window colour scale; which strength reference each class of material uses and which stress measure
  is compared with it; and the mesh-check limit. Every block, result, export and published package MUST
  follow it, and the automated tests MUST cover it.
- **FR-051**: Changing a convention MUST flag every stored run and every published scenario that depends on
  it for re-verification. A flagged scenario MUST NOT be republished until it is re-verified: its runs are
  re-run under the current conventions version, its verification note is renewed from those runs, and the
  flag is cleared only by that republication.

**Scenarios, building blocks and settings**

- **FR-003**: A scenario MUST be an assembly of building blocks on a jaw segment, with settings in five
  groups: patient bone, implant, prosthesis, bite and load, and model detail.
- **FR-004**: Building blocks MUST include: jaw segment (mandible or maxilla, any region up to a full arch,
  with its ridge height and width, the nerve canal in the mandible, the sinus floor in the maxilla and the
  positions of neighbouring roots), mucosa, natural tooth with periodontal ligament (intact or prepared as an
  abutment), implant, abutment, crown, pontic, splinting connector, cantilever extension, bar, attachment (at
  least ball, stud-type resilient and bar clip), denture base, artificial teeth, clasp, rest and major
  connector. Blocks are delivered in the slices set out in Story 1 and Story 9. This feature delivers the
  slice 1a and 1b blocks; the others arrive with their physics in the later Story 9 features.
- **FR-005**: The studio MUST offer prosthesis-type templates, each editable block by block: single crown
  (implant or tooth), splinted crowns, bridge (implant, tooth or mixed support), cantilever bridge, full-arch
  fixed prosthesis, implant overdenture, removable partial denture and complete denture. The owner MUST be able
  to save their own templates. Each template is delivered with the blocks it needs; this feature delivers the
  implant-supported fixed templates (single crown on an implant, splinted crowns, implant bridge, cantilever
  bridge, full-arch fixed).
- **FR-006**: Adding a new building block type or template later MUST NOT require changes to the run library,
  comparison, studies, exports or publishing.
- **FR-007**: Each block MUST expose its own settings within the five groups, including at least: bone (jaw,
  region, density class D1 to D4, cortical thickness, crestal bone level, ridge height and width, nerve canal
  position, sinus floor position, mucosa thickness); implant (diameter, length, thread design, connection
  type, position, angulation, healing state: osseointegrated or immediately loaded); natural tooth (tooth
  type, root length, bone support level, ligament properties); prosthesis (crown height, material, abutment
  type and angle, retention screw or cement, screw preload, connector size, cantilever length, denture base
  material and extension, attachment type).
- **FR-052**: Settings are of two kinds. Library settings (material properties, bone parameters, load values,
  strength references) MUST take their values from approved literature entries (FR-012). Design settings
  (sizes, positions, angles, counts and choices of type) are chosen freely by the owner within the limits the
  block allows. In loads, the force magnitude and the number of chewing cycles are library settings; the load
  direction (angle and azimuth), the contact points and their shares are design settings, and the assumptions
  note states them (FR-061). A library setting given a value outside its published range MUST carry an "outside published
  range" mark, and a run with that mark MUST NOT be publishable.
- **FR-008**: Bite and load settings MUST include force, direction and one or more contact points on any
  occlusal surface, and load patterns for a single bite, a chewing cycle and clenching or bruxism, each defined
  from literature entries. A single bite is one force at its contact points. A chewing cycle is a listed
  sequence of instants (contact points, direction and force share) solved by superposition, with results
  reported for the worst instant; its cycle count is kept for fatigue (later feature). Clenching or bruxism
  (one pattern, `clenching`) is a sustained force whose magnitude comes from its own literature entry.
- **FR-009**: Model detail settings MUST let the owner choose the detail level and see the expected run time
  and memory before starting.
- **FR-010**: Shapes MUST be generated automatically from the blocks and their settings. The studio MUST NOT
  accept a patient's own scan. Importing 3D model files is out of scope for this feature.
- **FR-011**: Assemblies that cannot exist physically (overlapping parts, unsupported pontics, implants in the
  nerve canal, sinus or a neighbouring root, implants longer than the available bone) MUST be refused before a
  run, with a plain explanation and the location of the problem. Collisions MUST also be shown while building,
  as soon as a block is placed or moved. A template opened on a jaw segment it does not fit MUST tell the
  owner what must change instead of creating a broken assembly.
- **FR-011a**: Each block type MUST declare the physics it needs (bonded elastic, periodontal ligament or
  contact). Each result type MUST declare any further physics it needs (screw preload for screw stress,
  contact for micromotion, fatigue for fatigue life). Every kind of physics, including bonded elastic, is
  "awaiting benchmark" until its known-answer benchmark passes. Scenarios containing a block whose physics has
  not passed its benchmark MUST be buildable and savable, but previews and runs of them MUST be refused,
  naming the missing benchmark. A result type whose physics has not passed MUST be shown as locked, naming the
  missing benchmark, without blocking the rest of the run.
- **FR-062**: If a benchmark fails when it is re-run, its physics MUST return to "awaiting benchmark", the
  blocks and result types that need it MUST lock again, and published scenarios that depend on it MUST be
  flagged for review. A scenario flagged for review MUST NOT be republished until the benchmark passes again
  and the owner records the review; the flag is cleared only by that republication.

**Literature library**

- **FR-012**: Every material property, bone parameter, load value and strength reference used in a run MUST
  come from an approved literature library entry.
- **FR-013**: Each entry MUST record the parameter, units, typical value, published range, every source
  (authors, year, title, DOI or PMID), study type, notes, status (proposed, approved or rejected) and
  approval date.
- **FR-014**: Entries proposed from literature searches and entries the owner adds from papers MUST follow
  the same approval step. Only approved entries MUST be selectable.
- **FR-015**: When choosing a value, the studio MUST show the published range and sources and let the owner
  pick a value within the range or a specific source's value, with the typical value preselected.
- **FR-016**: Editing an approved entry MUST create a new version; runs MUST keep the version they used.
- **FR-053**: Proposed entries MUST be addable both in bulk (prepared outside the studio, for example by
  Claude from literature searches) and one at a time by the owner. Both arrive with the status "proposed".
- **FR-054**: Before an entry can be approved, the DOI or PMID of each of its sources MUST be checked to
  resolve to a paper whose authors, year and title match. The check result MUST be shown to the owner, and an
  entry with an unresolved source MUST NOT be approvable.
- **FR-055**: Rejecting an entry that past runs used MUST flag those runs "uses a rejected entry". Flagged
  runs MUST NOT be publishable until they are re-run with approved entries.

**Running**

- **FR-017**: The studio MUST show a preview result within 10 seconds of a setting change, clearly labelled as
  a preview and never publishable. Beside each preview the studio MUST show how far previews differ from
  detailed results, as measured on the verification cases (the dental published-study cases once they exist,
  otherwise the textbook cases, saying which), so the owner knows how much to trust it.
- **FR-018**: A "detailed" run is a run at a detail level that passes the mesh check (FR-037) for that
  scenario, or for a verified model whose mesh justification it carries over. The detail levels are `preview`,
  `standard`, `fine` and `extra_fine`. Until the mesh check exists (Story 5), the `standard` level counts as
  detailed provisionally; timings measured that way MUST be confirmed again once the single implant crown's
  first mesh check shows which level passes. A detailed single implant crown run MUST finish within 30 minutes
  on the main simulation PC. A detailed run of any scenario with up to three implants or teeth MUST finish
  within one overnight period (10 hours). Larger assemblies (full arch, complete dentures) MUST show their
  estimate before starting; a level that does not pass the mesh check may be used to explore them, but such a
  run is not a detailed run and is not publishable.
- **FR-019**: Runs MUST be queued and executed unattended, one heavy run at a time, continuing when the browser
  is closed, with progress, estimated remaining time and cancel.
- **FR-020**: The owner MUST be able to queue a study that varies one or more settings over listed values.
- **FR-021**: The sensitivity button MUST create minimum, typical and maximum runs for a chosen library
  parameter and report the spread of each key result.
- **FR-022**: A failed run MUST record its reason and MUST NOT show partial results as complete.
- **FR-023**: Each run MUST record all settings, the library entry versions, the engine version, the model
  detail level, solver convergence information and its dates. For the direct linear solver, convergence
  information means, per unit load case: the force-balance check (the solver's total reaction against the
  applied load, per component); the moment-balance check (the reaction moment against the applied moment);
  the energy check (the work of the loads, Σ fᵢ·uᵢ, against twice the strain energy); and the solver's
  relative residual, where the solver reports one. A run whose force-balance, moment, energy or residual
  error exceeds its tolerance in the conventions document MUST fail with that reason. These checks verify the
  solve, not the model: an exact solution passes them whatever its boundary conditions. Before the solve, every part MUST be joined to the supported assembly through
  shared faces; otherwise the run fails with a plain reason naming the part.
- **FR-056**: After the main PC restarts or wakes, runs that were in progress MUST be marked "interrupted" and
  put back in the queue, queued runs MUST keep their place, and no interrupted run may be shown as complete.
- **FR-057**: Before a run or study starts, the studio MUST compare its expected memory and disk space with
  what the machine that will solve it has (the main PC, or the rented solver machine, FR-064). It MUST refuse a run whose expected memory is too high, suggesting a lower detail
  level, and MUST warn when disk space is short. A run that still runs out of memory MUST stop cleanly and be
  recorded as failed with that reason.
- **FR-058**: Stored meshes, previews and results MUST be tied to every input that affects them (settings,
  library entry versions, the engine code that affects results, solver build, conventions version). Changes
  that cannot affect results (interface, server, documentation) MUST NOT make stored items out of date
  (Constitution, Principle VI: re-solve only when inputs change). When any of these no longer matches, the
  stored item MUST be flagged "out of date", MUST NOT be reused silently, and MUST be re-run before it can be
  newly published (Constitution, Principle VI).
- **FR-064**: The owner MAY add a rented solver machine for heavy runs and studies that are too large or too
  slow for the main PC (Constitution, Scope: Compute). The studio, its database and every stored result stay
  on the main PC; previews always run on the main PC. A run sent to the rented machine MUST:
  - send only the generated solver input and receive only the solver output, over an encrypted,
    key-authenticated connection;
  - use a solver build identified like any other (version, equation solver, SHA-256) that has passed the
    benchmarks of every capability it needs on that machine; until then, runs there are refused, naming the
    benchmark (FR-011a);
  - use that machine's own estimates and memory limit (FR-057), and record which machine solved it;
  - fail cleanly with its reason, storing nothing partial, if the connection is lost (FR-022), and be
    re-queued like an interrupted run (FR-056) when the owner chooses;
  - leave no inputs or results on the rented machine when it finishes.

**Results**

- **FR-024**: The studio MUST show bone strain coloured by Frost windows, with a legend that cites its source
  and labels the windows as teaching references, and the crestal value for each implant and tooth as a region
  mean on the worse side (regions as defined in the conventions document). Singular corner peaks MUST NOT be
  reported.
- **FR-025**: The studio MUST show stress in the implant, abutment and crown as a percentage of each
  material's strength reference (mean and 95th percentile), with the reference's source and a "teaching
  reference" label. Screw stress is shown the same way once screw preload is verified (FR-059).
- **FR-059**: Screw stress, and any result derived from it, MUST become available only after the screw preload
  benchmark passes. Until then the screw MUST be listed as "not reported: needs screw preload".
- **FR-026**: The studio MUST show displacement values and a magnified movement animation with the
  magnification stated.
- **FR-027**: The studio MUST compare two or more runs side by side with one shared colour scale and one shared
  deformation scale, and list the differing settings and the differences in key results.
- **FR-028**: Every result view and export MUST carry the teaching-only notice.
- **FR-029**: Micromotion at the bone-implant surface MUST become available only after the contact benchmark
  passes (later phase).
- **FR-030**: Fatigue life estimates for the screw and implant MUST become available only after the fatigue
  benchmark passes (later phase). The screw estimate also needs the screw preload benchmark.

**Run library**

- **FR-031**: Scenarios and runs MUST be organised in projects with notes and tags, and be searchable by text,
  tag and setting value.
- **FR-032**: "Duplicate and change" MUST copy a scenario, keep a link to the original, and highlight the
  changed settings.
- **FR-033**: Finished results MUST be stored so they reopen without re-running.
- **FR-034**: The owner MUST be able to back up and restore all projects, runs and the literature library.

**Verification**

- **FR-035**: The studio MUST include a verification suite of textbook problems with exact answers and
  reproductions of 3 to 5 published dental implant finite element studies, each with a stated tolerance and a
  stored pass or fail report. Each case's compared quantity and tolerance MUST be recorded before the case is
  first run; a later change of tolerance MUST be recorded with its reason. Verification cases use the inputs
  stated by their own source (the textbook problem or the published study), cited in the case file; they are
  benchmarks, not scenario runs, so FR-012 does not apply to them.
- **FR-036**: Publishing MUST stay locked until every required verification case passes.
- **FR-037**: The owner MUST be able to run a mesh check (two detail levels) for any scenario and store the
  result in its verification note, marked pass or fail against the mesh-check limit in the conventions
  document (a 5% change in key results unless that document states otherwise).
- **FR-060**: Each scenario's verification note MUST contain: the comparison with cited published studies or
  benchmark cases where comparable ones exist, with any difference explained in writing; the mesh
  justification (the scenario's own mesh check, or a documented carry-over from a verified model at the same
  detail level); the solver convergence information; and the verification-suite report it relies on.
  Publishing MUST be refused while any of these is missing.
- **FR-061**: Each scenario's assumptions note MUST state: the geometry and its source; materials with
  references; loads (magnitude, direction, contact point); supports; interface assumptions (bonded, contact,
  preload); the model detail level; and the known limits, including the likely direction of error.

**Exports**

- **FR-038**: Figures MUST be print quality, with legend, scale, units, source line and teaching-only notice.
- **FR-039**: Teaching films MUST be widescreen videos of results and comparisons.
- **FR-040**: Slide animations MUST be short widescreen loops that Microsoft PowerPoint (the owner's
  presentation software) plays without conversion; an optional ready-made PowerPoint slide MUST contain the
  clip, title, one-line lesson and source.
- **FR-041**: Promo clips MUST be vertical, 15 to 45 seconds, with burned-in captions, a generated voice
  reading an owner-approved script (voice licence allowing commercial use), the teaching-only notice and an
  end card; they MUST be made only from published scenarios.
- **FR-042**: The run report MUST list every setting, literature entry and version, key result, limit and the
  verification status.
- **FR-043**: Raw numbers MUST be exportable as a spreadsheet file.
- **FR-044**: Every export MUST be regenerable from the stored run without manual editing.

**Publishing**

- **FR-045**: Publishing MUST be refused unless the scenario's checklist is complete: verification note,
  assumptions note, owner sign-off, frame-by-frame review (including on a phone-sized screen), tests passing,
  teaching-only notice, and all inputs from approved library entries. A refusal MUST name every missing item.
- **FR-046**: Publishing MUST produce a published scenario package containing everything the website
  (feature 001) shows: title; branch and prosthesis type; situations; keywords; one-sentence teaching message;
  explanation; key numbers (with units, and whether each is a region mean or a 95th percentile); visuals
  (each with its still picture, scales and stated magnification); interactive page data where the scenario
  has it (FR-063); assumptions note; verification note; references; access level; teaching-only notice; and
  its version, publication date, engine version and library entry versions. This list is the contract between
  the studio and the website.
- **FR-047**: Unpublishing and republishing MUST be recorded, and earlier published versions MUST be kept. A
  scenario changed after publishing MUST leave its published version unchanged until the owner republishes.
- **FR-063**: For a scenario being published, the owner MUST be able to produce interactive page data: key
  results and result pictures over stated ranges of chosen controls (for example bite angle, contact position,
  bite force, jaw and bone type), built from stored detailed runs. The data MUST record its control ranges and
  the solved cases it is built from. Values between solved cases MUST be derived by a method stated in the
  verification note, with its error. Nothing may be extrapolated beyond the solved ranges.

**Safety and neutrality**

- **FR-048**: The studio MUST NOT contain fields for patient identity or patient data.
- **FR-049**: Published content MUST NOT include brand names, except in a scenario explicitly marked as a
  neutral comparison of commercial designs. Such a scenario MUST apply identical loads, bone and conventions
  to every product, cite each geometry source and disclose any relationship with the manufacturers
  (Constitution, Principle VII).

### Key Entities

- **Building block**: one part of an assembly (jaw segment, mucosa, natural tooth, implant, abutment, crown,
  pontic, connector, cantilever, bar, attachment, denture base, artificial teeth, clasp, rest, major
  connector). Defines its settings, where it can attach, the result regions it reports, and the physics it
  needs.
- **Conventions document**: the single, versioned definition of units, coordinate frame, load-angle sign,
  reporting regions, Frost colour scale, strength references and the mesh-check limit that every scenario
  follows.
- **Physics capability**: a kind of physics (bonded elastic, screw preload, periodontal ligament, contact,
  fatigue) with its benchmark status ("ready" or "awaiting benchmark"). Blocks and result types name the
  capabilities they need.
- **Prosthesis-type template**: a ready-made assembly of blocks (single crown, splinted crowns, bridge,
  cantilever bridge, full arch, overdenture, partial denture, complete denture, or one saved by the owner).
- **Scenario**: one assembly of blocks with chosen settings and loads; belongs to a project; may link to the
  template or scenario it came from.
- **Literature entry**: one parameter with units, typical value, published range, sources, status and
  versions.
- **Source**: a publication (authors, year, title, DOI or PMID, study type) supporting one or more entries.
- **Run**: one solve of a scenario at a detail level; holds status, settings snapshot, library entry versions,
  engine version, convergence information and results.
- **Study**: a queued group of runs (parameter sweep or sensitivity check) with its summary.
- **Project**: a named collection of scenarios, runs and studies with notes and tags.
- **Verification case**: a benchmark with expected value, tolerance and stored pass or fail reports.
- **Verification note / Assumptions note**: the per-scenario records required for publication, with the
  contents listed in FR-060 and FR-061.
- **Export**: a figure, film, slide animation, slide, promo clip, run report, numbers file or interactive page
  data produced from stored runs.
- **Published scenario package**: the versioned, public form of a scenario that the website reads, with the
  fields listed in FR-046.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The owner can create a scenario from any template, change it, and start a detailed run in under
  5 minutes after opening the studio.
- **SC-001a**: Every prosthesis-type template delivered by this feature (slice 1a: single implant crown;
  slice 1b: the implant-supported fixed templates) can be built, changed block by block, saved and run in
  detail. The remaining templates are measured the same way in the later Story 9 features.
- **SC-002**: Preview results appear within 10 seconds of a setting change.
- **SC-003**: A detailed single implant crown run finishes in 30 minutes or less on the main simulation PC.
- **SC-004**: An overnight study of at least 18 detailed single implant crown runs completes unattended within
  10 hours.
- **SC-005**: 100% of inputs in every run report trace to approved literature entries with their sources.
- **SC-006**: Before the first publication, the engine passes 100% of textbook cases and reproduces at least 3
  published studies within the tolerances recorded before each case was first run.
- **SC-007**: Repeating a stored run with the same settings and library versions reproduces its key numbers
  within 0.1%.
- **SC-008**: A side-by-side comparison of two finished runs is ready in under 1 minute.
- **SC-009**: 100% of slide animations tested play in the owner's presentation software on the first try.
- **SC-010**: Once a scenario's checklist is complete, publishing it takes under 15 minutes.
- **SC-011**: The studio cannot be opened from outside the home network (tested from a phone on mobile data).
- **SC-012**: When a new building block type or template is added later, the run library, comparison,
  studies, exports and publishing work for it without changes to them.
- **SC-013**: A detailed run of any scenario with up to three implants or teeth finishes within 10 hours on the
  main simulation PC.
- **SC-014**: The conventions document exists and its tests pass before the first scenario is run.
- **SC-015**: 100% of the solved cases in a package's interactive page data equal the stored runs' key numbers,
  and the website's interactive view can be built from the package alone.

## Assumptions

- The studio is built from scratch. The earlier `fea-implant-teaching/` project is reference material only;
  its verified numbers may be used as benchmark cases.
- The owner is the only user. The interface is in English.
- The main simulation PC has an RTX 4060 Ti, 10 physical cores and about 32 GB of memory; the laptop has
  24 GB. Both run Windows and share the home network. Overnight runs are acceptable. Measured on the main PC,
  32 GB holds direct-solver models up to about 1.2 M degrees of freedom (research R8). If a model is too large
  or too slow for it, the owner may rent a more powerful machine as a solver machine (FR-064).
- Only free, open-source simulation tools are used (Constitution, Scope).
- This feature includes a seed literature library for the bonded-elastic blocks (bone, implant, abutment,
  screw, crown, pontic, connector materials and bite loads), proposed by Claude and approved by the owner.
  Entries for screw preload, the periodontal ligament, mucosa, denture materials and fatigue data are added
  with their physics in Story 9.
- The generated voice for promo clips comes from a service whose licence allows commercial use; it may have a
  cost, chosen during planning. Claude may draft clip scripts; the owner approves them.
- The website (feature 001) is built after this feature; this feature defines the published scenario package
  it reads (FR-046, FR-063), and spec 001 was revised on 2026-11-05 to match.
- The builder is block-based from slice 1a, so every prosthesis type in the year-one plan (implant,
  tooth-supported and removable) fits it. Blocks and physics arrive in this order: the single implant crown
  (slice 1a) and multi-implant fixed prostheses (slice 1b), both on bonded elastic (Stories 1 to 8); then
  screw preload, the periodontal ligament, contact and fatigue (Story 9), each with the blocks that need it
  and each after its benchmark. A block delivered before its benchmark passes can be built and saved but not
  run.
- Defaults chosen during the 2026-11-05 spec check, which the owner may change: the owner password (FR-001),
  the 5% mesh-check limit (FR-037), out-of-range values allowed with a mark that blocks publication (FR-052),
  and the 18-run overnight target (SC-004).
- Defaults chosen during the 2026-11-05 cross-artifact analysis, which the owner may change: load direction and
  contact shares are design settings, not library settings (FR-052); verification cases use their own sources'
  inputs (FR-035); a chewing cycle is reported at its worst instant (FR-008).
- 3D file import is out of scope for this feature.
- Full-arch and complete-denture models are the largest; they may need a lower detail level, an overnight
  run on the main simulation PC, or the rented solver machine (FR-064).
- Story 9 is split into separate later features (screw preload, ligament, contact, fatigue); this feature
  delivers only its lock and unlock framework (see Story 9, "Scope in this feature").
