# Feature Specification: Dental Simulator Platform MVP

**Feature Branch**: `001-platform-mvp`
**Created**: 2026-11-05
**Status**: Draft
**Input**: User description: "Dental Simulator platform shell (laptop textbook layout, phone card layout,
find-by-situation, assumptions page) with the existing single implant crown scenarios as first content"
**Revision (2026-11-05, after the independent spec check)**: the project is built from scratch (Constitution
2.0.0). Launch content comes from scenarios solved, verified and published in the Simulation Studio (feature
002), not from the earlier project's results. The studio enforces the publication checklist; the website
shows only published scenario packages. Films are view-only (owner decision). This feature is built after
feature 002.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Open and understand a scenario on a laptop (Priority: P1)

A dental resident hears about Dental Simulator and opens it on a laptop. The home page shows a contents
list on the left, grouped into three branches: implant prostheses, tooth-supported prostheses and
removable prostheses. Under "Single implant crown" the resident picks "Bite angle: straight vs oblique".
The scenario opens on the right: a one-sentence teaching message, a short film of the bone colours changing
as the bite tilts, the key numbers with units, a short explanation, a visible "for teaching, not
patient-specific prediction" notice and a link to the scenario's assumptions. The resident leaves knowing
the one point the scenario teaches.

**Why this priority**: this is the core value of the platform. A library that a learner can open and learn
from is a working product even with nothing else.

**Independent Test**: publish one scenario, open the platform on a laptop, reach the scenario from the
home page and confirm that every required part of the scenario page is present and the film plays.

**Acceptance Scenarios**:

1. **Given** the home page on a laptop-width screen, **When** the visitor arrives, **Then** a contents list
   grouped by the three branches is visible next to the open topic, and branches without published
   scenarios show their planned prosthesis types marked "coming".
2. **Given** the contents list, **When** the visitor selects a published scenario, **Then** the scenario page
   shows its title, teaching message, primary visual, key numbers with units, explanation, teaching-only
   notice, assumptions link and references.
3. **Given** a scenario page with a film, **When** the visitor presses play, **Then** the film plays inside the
   page and can be paused and replayed.
4. **Given** a scenario's direct link copied from the address bar, **When** someone else opens that link,
   **Then** the same scenario opens directly.

---

### User Story 2 - Explore a scenario interactively (Priority: P2)

A resident wants to see for themself how the bite changes the bone. On the "Single implant crown"
interactive view they move a bite-angle control across its range, slide the contact point from the
lingual cusp tip to the buccal cusp tip, raise the bite force, and switch between mandible (D2 bone) and
maxilla (D4 bone). The bone colours, the crestal strain value and the component stresses (as a percentage of
each strength reference) update immediately. The Frost-window colour legend stays visible, with its source.

**Why this priority**: interaction is what separates the platform from textbooks and papers, but the
library is still valuable without it.

**Independent Test**: open the interactive view, move each control across its range and confirm that the
picture and numbers change at once and match the values in the published scenario package.

**Acceptance Scenarios**:

1. **Given** the interactive view, **When** the visitor moves any control, **Then** the picture and numbers
   update without a perceptible delay.
2. **Given** the controls set to a solved case in the published package (for example 100 N at 30° on the
   central fossa), **When** the values are read, **Then** they equal the package's values.
3. **Given** two panels shown side by side (mandible and maxilla), **When** any control changes, **Then**
   both panels keep one shared colour scale and one shared deformation scale, and any magnification is
   stated on screen.
4. **Given** any setting where bone strain falls in the "pathologic" window, **When** it is displayed,
   **Then** the teaching-only notice remains visible and the window is labelled as a teaching reference.

---

### User Story 3 - Use the platform on a phone (Priority: P3)

A dental student finds Dental Simulator through a short video on social media and opens it on a phone.
The home page shows scrolling picture cards grouped by branch. Tapping a card opens the scenario with the
film first. The interactive view works with touch, and nothing needs horizontal scrolling.

**Why this priority**: most visitors arriving from promo videos will be on phones, but the content must
exist first.

**Independent Test**: open the platform on a phone-width screen, reach a scenario from the home page and
use its film and interactive view with touch only.

**Acceptance Scenarios**:

1. **Given** a phone-width screen, **When** the home page opens, **Then** scenarios appear as picture
   cards grouped by branch, and no content requires horizontal scrolling.
2. **Given** a card, **When** the visitor taps it, **Then** the scenario page opens with its primary visual
   at the top.
3. **Given** the interactive view on a phone, **When** the visitor drags any control with a finger, **Then**
   it behaves the same as on a laptop.

---

### User Story 4 - Find scenarios by clinical situation (Priority: P4)

A dentist has a patient with a missing lower first molar and wants to see the options. From the home page
they choose "Find by situation", pick "Single missing posterior tooth - mandible", and see every scenario
that applies, plus the prosthesis types planned for that situation marked "coming".

**Why this priority**: it matches how clinicians think, but the contents list and cards already give full
access to every scenario.

**Independent Test**: choose each listed situation and confirm that the results contain exactly the
published scenarios tagged with that situation, plus planned items marked "coming".

**Acceptance Scenarios**:

1. **Given** "Find by situation" on either layout, **When** the visitor picks a situation, **Then** every
   published scenario tagged with it is listed, and planned prosthesis types for that situation are shown as
   "coming".
2. **Given** a situation with no published scenarios, **When** it is picked, **Then** the visitor sees what is
   planned for it instead of an empty page.
3. **Given** the search box, **When** the visitor types a term from a scenario's title or keywords, **Then**
   that scenario appears in the results.

---

### User Story 5 - Check the assumptions and limits (Priority: P5)

A lecturer who is sceptical of finite element results wants to know what the simulations assume before
showing them to students. From any scenario page they follow the assumptions link, and from the main menu
they open "Assumptions and Limits", "How to read the colours" and "What these simulations cannot tell
you".

**Why this priority**: trust from educators is the platform's long-term asset (Constitution, Principle
III), but it supports the content and does not replace it.

**Independent Test**: from every published scenario, follow the assumptions link and confirm that the note
covers every required item.

**Acceptance Scenarios**:

1. **Given** any published scenario, **When** the visitor opens its assumptions note, **Then** it states the
   geometry and its source, the materials with references, the loads, the supports, the interface
   assumptions, the model detail level and the known limits, including the likely direction of error.
2. **Given** the "Assumptions and Limits" page, **When** it is opened, **Then** it lists every published
   scenario with a link to its note.

---

### User Story 6 - Add a published scenario to the library (Priority: P6)

The project owner (a prosthodontist) has published a verified scenario from the Simulation Studio (feature
002). The studio enforces the publication checklist and produces a published scenario package. The owner adds
the package to the website. The scenario then appears in the contents list, the cards, search and
find-by-situation, with no change to the platform's layout or navigation.

**Why this priority**: the year-one target of 30 scenarios depends on adding scenarios cheaply.

**Independent Test**: add a complete package and confirm the scenario appears everywhere it is tagged; add a
package with one required field missing and confirm it is refused with the field named; withdraw a package
and confirm the scenario disappears.

**Acceptance Scenarios**:

1. **Given** a scenario that has not been published from the studio, **When** a visitor browses or searches,
   **Then** the scenario does not appear anywhere.
2. **Given** a package with a required field missing, **When** the owner adds it, **Then** the website
   refuses it and names what is missing.
3. **Given** a complete package, **When** the owner adds it, **Then** the scenario appears in the contents
   list, cards, search and every situation it is tagged with.
4. **Given** a scenario the owner has unpublished in the studio, **When** the website is updated, **Then**
   the scenario no longer appears and its direct link shows the short "no longer available" message.

---

### User Story 7 - See how many people use the platform (Priority: P7)

The owner wants to know whether the platform is reaching the 50-user target and which scenarios are
watched most.

**Why this priority**: it measures success but does not change what learners get.

**Independent Test**: visit the platform from two devices, open three scenarios, and confirm that the
owner's counts show the visits and the scenario views.

**Acceptance Scenarios**:

1. **Given** visits to the platform, **When** the owner checks the counts, **Then** they see unique visitors
   and views per scenario for a chosen date range.
2. **Given** a visitor who blocks counting, **When** they use the platform, **Then** every feature still
   works.

---

### Edge Cases

- A visitor opens a direct link to a scenario that was later unpublished: they see a short message and the
  link to the library, not an error page.
- A visitor taps a "coming" item: they see what is planned, and nothing breaks.
- A visitor on a slow connection: each film shows a still picture until it is played, and the scenario's
  text and numbers appear before the film has loaded.
- A device that cannot play the film: the still picture and the text explanation still teach the point.
- A phone rotated between portrait and landscape: the layout adapts without losing the visitor's place or
  control settings.
- A very small screen (320 px wide): every control remains usable and no text is cut off.
- Interactive controls can only reach values inside the scenario's published ranges; there is no
  extrapolation beyond them.
- A situation or search with no matching scenario: the visitor sees what is planned, or a "no results"
  message with a link back to the library.

## Requirements *(mandatory)*

### Functional Requirements

**Library and navigation**

- **FR-001**: The home page MUST present the library in three branches: implant prostheses, tooth-supported
  prostheses and removable prostheses. Planned prosthesis types without published scenarios MUST be shown
  as "coming".
- **FR-002**: On laptop-width screens the platform MUST show a persistent contents list beside the open
  topic.
- **FR-003**: On phone-width screens the platform MUST show scenarios as picture cards grouped by branch.
  Tapping a card opens the scenario.
- **FR-004**: Both layouts MUST offer "Find by situation". At launch the situations MUST include single
  missing posterior tooth (mandible) and single missing posterior tooth (maxilla). Further situations MUST
  be addable with new content.
- **FR-005**: The platform MUST offer a search across scenario titles and keywords.
- **FR-006**: Every scenario MUST have a stable direct link that opens that scenario.

**Scenario pages**

- **FR-007**: Each scenario page MUST show its title, a one-sentence teaching message, a primary visual
  (film, interactive view or side-by-side comparison), a short explanation, the key numbers with units, the
  teaching-only notice, a link to its assumptions note, and its references.
- **FR-008**: Films MUST play inside the page with play, pause and replay, MUST show a still picture before
  playing, and MUST be legible on a phone-width screen.
- **FR-009**: An interactive view MUST provide the controls defined in the scenario's interactive page data
  (feature 002, FR-063). For the single implant crown these are bite angle, contact position (lingual cusp
  tip to buccal cusp tip), bite force, and jaw and bone type. Each control's range MUST be the published
  range for that scenario, with no extrapolation beyond it.
- **FR-010**: Interactive views MUST show the crestal bone strain in microstrain and the stress in each
  reported component (implant, abutment and crown, and the screw when the package includes it) as a
  percentage of its strength reference, with the reference's source and a "teaching reference" label. Values
  are region means or 95th percentiles, as stated in the package.
- **FR-011**: A colour legend of the Frost strain windows MUST be visible whenever bone strain is coloured.
  The legend MUST cite its source and label the windows as teaching references.
- **FR-012**: Panels compared side by side MUST share one colour scale and one deformation scale, and any
  deformation magnification MUST be stated on screen.

**Trust and safety**

- **FR-013**: The platform MUST provide an "Assumptions and Limits" page listing every published scenario,
  each linked to an assumptions note covering geometry and source, materials with references, loads,
  supports, interface assumptions, model detail level and known limits, including the likely direction of
  error.
- **FR-014**: The platform MUST provide a "How to read the colours" page and a "What these simulations cannot
  tell you" page, reachable from every page.
- **FR-015**: The teaching-only notice MUST appear on every scenario page and in the site footer. It MUST NOT
  be hidden by any setting.
- **FR-016**: The platform MUST NOT ask for or accept patient data, and MUST NOT offer patient-specific
  simulation.
- **FR-017**: The library MUST NOT show implant or material brand names or sponsored content. The only
  exception is a scenario published as a neutral comparison of commercial designs (Constitution, Principle
  VII), which MUST show its geometry sources and any disclosed relationship with the manufacturers.

**Content publishing**

- **FR-018**: On the website a scenario is either published (shown in full) or coming (planned, shown as a
  placeholder). Drafts stay in the studio and MUST NOT be visible to visitors.
- **FR-019**: The website MUST show only scenarios delivered as published scenario packages from the studio,
  which enforces the publication checklist (feature 002, FR-045). The website MUST refuse a package that
  lacks any required field (feature 002, FR-046) and MUST name what is missing.
- **FR-020**: Adding a scenario MUST NOT require changes to the platform's layout or navigation. A published
  scenario MUST appear automatically in the contents list, cards, search and every situation it is tagged
  with.
- **FR-021**: Every number shown on the platform MUST equal the number in the published scenario package it
  comes from.
- **FR-022**: Each scenario MUST carry an access level. At launch every scenario is "free", so that paid
  content can be introduced later without restructuring the library.
- **FR-023**: Films MUST be view-only on the platform: no download button or download link is offered (owner
  decision 2026-11-05, to protect the planned paid lecture packs).

**Measurement and access**

- **FR-024**: The platform MUST count unique visitors and views per scenario without collecting personal
  data, and the owner MUST be able to view these counts for a chosen date range.
- **FR-025**: Every feature MUST work for visitors who block counting.
- **FR-026**: The platform MUST be usable without sign-up and MUST be in English.
- **FR-027**: On laptops, every control and link MUST be reachable with the keyboard alone, and text MUST
  meet the WCAG 2.2 AA contrast level.

### Key Entities

- **Branch**: a top-level family of prostheses (implant, tooth-supported, removable). Holds prosthesis types.
- **Prosthesis type (topic)**: for example "Single implant crown" or "Cantilever bridge". Belongs to one
  branch. Has a status of "coming" until it has at least one published scenario.
- **Scenario**: the unit of content and of the year-one target, for example "Bite angle: straight vs
  oblique". It has a title, teaching message, prosthesis type, situations, keywords, status, access level,
  primary visual, key numbers, explanation, references, assumptions note and verification note, plus its
  package version and publication date.
- **Published scenario package**: the versioned form of a scenario produced by the studio (feature 002,
  FR-046). It is the only way content enters the website.
- **Situation**: a clinical situation used by "Find by situation", for example "Single missing posterior
  tooth - mandible". Linked to scenarios and to planned prosthesis types.
- **Visual**: a film, interactive view or side-by-side comparison belonging to a scenario, with its still
  picture, scales and stated magnification. An interactive view is built from the package's interactive page
  data.
- **Assumptions note**: the per-scenario statement of geometry, materials, loads, supports, interfaces,
  model detail level and limits, including the likely direction of error.
- **Verification note**: the per-scenario record of checks against published studies or benchmark cases,
  mesh justification and solver residuals.
- **Visit count**: anonymous counts of unique visitors and scenario views by date.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: At launch, at least 5 single implant crown scenarios are published, and 100% of them come from
  a complete studio package.
- **SC-002**: A first-time visitor can reach any published scenario from the home page in 3 clicks or taps
  or fewer, and within 30 seconds.
- **SC-003**: Interactive views update within a tenth of a second of a control moving, on a mid-range phone.
- **SC-004**: A scenario's text and key numbers appear within 3 seconds on a typical 4G mobile connection.
- **SC-005**: Every page is usable on screens from 320 px wide to large laptop screens, with no horizontal
  scrolling.
- **SC-006**: 100% of the solved cases in each published package are reproduced exactly by the platform's
  numbers.
- **SC-007**: 100% of scenario pages show the teaching-only notice and a working assumptions link.
- **SC-008**: In a test with at least 5 dental residents or students, at least 4 of them correctly state a
  scenario's teaching message after viewing it, without help.
- **SC-009**: Once the studio has produced a scenario's package, the owner can add it to the website in under
  15 minutes, with no change to layout or navigation.
- **SC-010**: The owner can see unique visitors and scenario views for any date range since launch.

## Assumptions

- The launch content is single implant crown scenarios built, solved, verified and published in the
  Simulation Studio (feature 002). This feature runs no simulations. Candidate launch topics are: bite angle,
  contact point (fossa vs cusps), bite force (how hard before overload), bone quality (D2 vs D4), the crown
  and implant deforming under load, and metal overload at high force. The owner chooses at least 5 for
  launch.
- This feature depends on feature 002 and is built after it. It reads the published scenario package (002,
  FR-046) and the interactive page data (002, FR-063) defined there.
- The earlier `fea-implant-teaching/` project, including its Implant Load Explorer page and teaching films,
  is reference material only. It may inspire the layout of the visuals, but none of its results, parameters
  or files are published.
- The project owner is the single clinical reviewer (Constitution, Principle II).
- Promo videos, the other implant prostheses, and the tooth-supported and removable branches are out of
  scope for this feature. They appear only as "coming".
- Accounts, payments and paid content are out of scope. Only the access-level label (FR-022) is included.
- Visitors use a browser released in the last two years on a laptop or phone.
- The platform is reached at a public web address. Choosing the final domain name does not block this
  feature.
- Visitor counting uses a method that needs no cookie consent banner and stores no personal data.
