# Quickstart: Simulation Studio

How to set up the studio, and how to check each user story works. Commands use PowerShell on Windows 11.

## 1. One-time setup

### On the main PC (runs the studio)

1. **Get the code**: clone the repository, for example to `D:\simulator`.
2. **Install the tools**:
   - **uv**: `winget install astral-sh.uv`
   - **Node 24 LTS**: needed only to build the interface.
   - **CalculiX 2.23, the open-source build with PaStiX** (research R3; steps in `docs/setup/solver.md`):
     1. Put `ccx_static.exe` from the dhondt.de Windows package in `D:\DentalSimTools\calculix\` (no other DLLs).
     2. Set `DENTALSIM_CCX` to that executable.
     3. Check it with `dentalsim solver check`, which prints the version, the equation solver (PaStiX) and the SHA-256.
3. **Install the environment and build the interface**:
   ```powershell
   cd D:\simulator
   uv sync                                  # Python 3.12 environment from uv.lock
   npm --prefix studio/ui ci; npm --prefix studio/ui run build
   ```
4. **First start**:
   ```powershell
   uv run dentalsim studio set-password
   uv run dentalsim studio serve            # creates D:\DentalSimData and a HTTPS certificate on first start
   ```
5. **Windows Firewall**: allow the studio port only on the Private profile and only from the local subnet. The
   first start prints the exact command.
6. **Router**: make sure no port forwarding points at the main PC.
7. **Check from outside the network (SC-011)**: on a phone using mobile data, open the studio address. It must
   fail.

### On the laptop (and on the main PC's own browser)

1. Open `https://<main-pc-name>:8443`.
2. Trust the studio certificate once. The first start prints how.
3. Sign in with the owner password.

### Development on the laptop

- `uv sync`, then `uv run dentalsim check` runs the full test gate.
- `npm --prefix studio/ui run dev` serves the interface with hot reload, against a local server.

## 2. Before the first scenario (slice 1a gate)

1. `uv run dentalsim conventions check`: the conventions document parses and its tests pass (SC-014).
2. `uv run dentalsim library import content/library/proposals/seed-bonded-elastic.yaml`, then approve each entry
   in **Library**. Approval needs every source check to pass.
3. `uv run dentalsim verify --physics bonded_elastic`:
   - Every textbook case must pass.
   - **Physics** then shows bonded elastic as "ready".
   - Before this, any preview or run is refused and names the benchmark (Story 1, scenario 7).

## 3. Checking each story

| Story | Check |
|---|---|
| 1a | **New scenario → template "Single crown (implant)"**. Move the implant into the nerve canal: the collision shows at once, and starting a run is refused with the location. Move it back and change the crown height: the preview updates within 10 s, labelled "preview", with its accuracy note. **Estimate → Run (standard)**: the estimate shows first, progress and remaining time while it runs, and it finishes within 30 min on the main PC. Open the results: Frost-coloured strain with legend and source, crestal value on the worse side, % of strength (mean and p95), movement animation with magnification, teaching-only notice, and the screw shown as "not reported: needs screw preload". |
| 1b | Template **"Cantilever bridge"** on two implants. Delete one implant: the unsupported part is reported. Restore it, save, and run in detail. |
| 2 | Import one proposal and add one entry by hand. Approving an entry with a mismatching DOI is refused. Approve one and reject the other: only the approved one can be chosen. Edit the approved one: a new version appears, and old runs still show v1. |
| 3 | Project "Implant length study". **Duplicate and change** the length from 10 to 8 mm and run both. **Compare**: shared scales, the changed setting listed, differences in key numbers, ready in under 1 min. Search by tag and by note text. |
| 4 | Queue a 6-run sweep and one sensitivity study, close the browser, restart the main PC midway: the interrupted run is requeued, and in the morning every run is done (or the failure is explained) and the spread summary is shown. |
| 5 | **Verification → Run all**: each case shows expected, computed, tolerance and pass or fail. While a required published-study case fails, **Publish** is refused and names it. Run a mesh check: the change in key results is stored, pass or fail. |
| 6 | From one run, export a figure, film, slide loop, `.pptx`, report and spreadsheet. Open the `.pptx` in PowerPoint: the clip plays. **Regenerate** each export: the content is identical. |
| 7 | Publish with the sign-off missing: refused, naming the missing item. Complete it and publish: `content/scenarios/<slug>/v1/package.json` matches [contracts/published-package.md](contracts/published-package.md). Build the interactive data: the solved cases equal the stored runs. Unpublish and republish: the history is kept. |
| 8 | Promo clip from an unpublished scenario: refused. From a published one: vertical, 15–45 s, captions, generated voice, notice and end card. |
| 9 | (Later increments) Each physics stays locked with its benchmark named until `dentalsim verify --physics <id>` passes. |

## 4. Backup

- `uv run dentalsim backup --to E:\Backups` (an external drive) makes one archive of the database, the store,
  published media and a library export.
- `dentalsim restore <archive>` checks every hash before restoring.
