# Contract: Studio HTTP API

Only the studio's own browser interface uses this API. It is served by the main PC over HTTPS on the home
network (research R10).

- **Data**: all bodies are JSON unless stated otherwise.
- **Errors**: every error uses one shape (below).
- **Sign-in**: every route except `POST /api/auth/login` needs a valid session cookie.
- **Home network only**: requests from outside the home subnets are closed before routing, with no reply.

## Error shape

```json
{
  "code": "run_refused",
  "message": "Plain-language explanation for the owner.",
  "problems": [
    {"code": "in_nerve_canal", "block": "implant-1", "location_mm": [12.0, 0.4, -9.1],
     "message": "Implant apex is 0.6 mm inside the nerve canal."}
  ],
  "missing": [{"item": "benchmark:bonded_elastic", "message": "Bonded-elastic textbook benchmarks have not passed."}]
}
```

| `code` | HTTP | Used when |
|---|---|---|
| `not_signed_in` | 401 | There is no session, or it has expired |
| `validation_failed` | 422 | Settings are outside their limits, or a library version is not approved |
| `run_refused` | 409 | Invalid assembly (`problems`) or a missing benchmark (`missing`) |
| `estimate_too_large` | 409 | Expected memory is above the limit; the message suggests a lower detail level |
| `publish_refused` | 409 | One or more checklist items fail, all listed in `missing` |
| `not_found` | 404 | |
| `conflict` | 409 | The scenario revision changed while it was being edited |
| `out_of_date` | 409 | A stored item no longer matches the current engine, conventions or library, so it cannot be reused (FR-058) |

## Auth and system

| Method and path | Purpose |
|---|---|
| `POST /api/auth/login` `{password}` | Starts a session: HttpOnly, Secure, SameSite=Strict cookie. Attempts are rate-limited. |
| `POST /api/auth/logout` | Ends the session |
| `GET /api/system` | Engine version, `dev_build`, solver build id, conventions version, memory and free disk on the main PC, queue summary |
| `POST /api/system/backup` → job | Full backup (FR-034) |
| `POST /api/system/restore` `{archive}` → job | Restore, with hash check |
| `GET /api/events` (Server-Sent Events) | Live updates, listed below |

Event types: `preview.ready`, `run.progress {run_id, percent, stage, remaining_s}`, `run.finished`, `study.progress`,
`job.failed`, `validity.changed`, `capability.changed`.

## Catalogue

| Method and path | Purpose |
|---|---|
| `GET /api/physics` | Capabilities with status and report link (FR-011a) |
| `GET /api/blocks` | Block types: settings schema (group, kind, limits), attach points, `needs_physics`, result types, status |
| `GET /api/templates` / `POST /api/templates` | List built-in and owner templates; save the current scenario as an owner template |
| `GET /api/conventions` | The parsed conventions block, including colour scales and regions |

## Projects and scenarios

| Method and path | Purpose |
|---|---|
| `GET/POST /api/projects`, `GET/PATCH/DELETE /api/projects/{id}` | Projects with notes and tags |
| `POST /api/scenarios` `{project_id, from: {template_id} or {scenario_id}}` | Create a scenario from a template, or duplicate one (FR-032). Returns `template_misfit` problems when the template does not fit the jaw segment. |
| `GET /api/scenarios/{id}` / `PUT /api/scenarios/{id}` `{revision, scenario}` | Read or save. The save body must carry the current `revision`. |
| `POST /api/scenarios/{id}/validate` | Runs the validity checks and returns a ValidityResult. Also triggered automatically on every block move (FR-011). |
| `GET /api/scenarios/{id}/diff/{other_id}` | Lists the settings that differ (FR-032, FR-027) |
| `GET /api/search?q=&tag=&setting=path:op:value` | Search across projects, scenarios and runs (FR-031) |

## Preview, estimate, run

| Method and path | Purpose |
|---|---|
| `POST /api/scenarios/{id}/preview` → `{preview_id}` | Queued in the preview lane. Answers `preview.ready` within 10 s. Refused with `run_refused` when a benchmark is missing. |
| `GET /api/previews/{id}` | Key numbers, `.glb` links, `accuracy_note`, `label: "preview"` |
| `POST /api/scenarios/{id}/estimate` `{detail_level}` | `{dof, time_s, memory_gb, disk_gb, fits: bool, warnings[]}` (FR-009, FR-057) |
| `POST /api/scenarios/{id}/runs` `{detail_level}` → `{run_id}` | Queued in the heavy lane |
| `GET /api/runs/{id}` | Run record: status, flags, versions, convergence, `locked_results` |
| `POST /api/runs/{id}/cancel` | |
| `GET /api/runs/{id}/results` | Key numbers (with statistic, region, side, reference, % of reference) and visuals |
| `GET /api/runs/{id}/assets/{name}.glb` | Binary glTF: surface mesh, result attributes, displacement morph target |
| `GET /api/queue` | Both lanes in order, with estimates |

## Compare and studies

| Method and path | Purpose |
|---|---|
| `POST /api/compare` `{run_ids[], fields[]}` | One shared colour scale and one shared deformation scale per field, settings that differ, and differences in key numbers (FR-027). Ready in under 1 minute (SC-008). |
| `POST /api/studies` `{project_id, base_scenario_id, kind: "sweep", axes}` | Parameter sweep (FR-020) |
| `POST /api/studies` `{..., kind: "sensitivity", parameter: "<library setting path>"}` | Minimum, typical and maximum runs (FR-021) |
| `GET /api/studies/{id}` / `POST /api/studies/{id}/cancel` | Study status and summary of the spread |

## Literature library

| Method and path | Purpose |
|---|---|
| `GET /api/library/entries?status=&category=&q=` | List entries |
| `GET /api/library/entries/{id}` | The entry with every version and source |
| `POST /api/library/entries` | Owner adds an entry; status `proposed` (FR-053) |
| `POST /api/library/import` (multipart YAML) | Bulk proposals, in the format of [library-entry-format.md](library-entry-format.md) |
| `POST /api/library/entries/{id}/versions/{v}/check-sources` | Crossref and NCBI check (FR-054) |
| `POST /api/library/entries/{id}/versions/{v}/approve` | Refused unless every source is `resolved_match` |
| `POST /api/library/entries/{id}/versions/{v}/reject` `{reason}` | Flags the runs that used it (FR-055) |
| `PUT /api/library/entries/{id}` `{changes}` | Creates a new version (FR-016) |
| `POST /api/library/export` | Writes a snapshot to `content/library/snapshots/` |

## Verification

| Method and path | Purpose |
|---|---|
| `GET /api/verification/cases` | Cases with quantity, expected value, tolerance, required flag and tolerance history |
| `POST /api/verification/run` `{case_ids?}` → job | Produces a VerificationReport and updates capability status (FR-062) |
| `GET /api/verification/reports/{id}` | |
| `POST /api/scenarios/{id}/mesh-check` `{levels}` → job | FR-037 |

## Exports

| Method and path | Purpose |
|---|---|
| `POST /api/exports` `{kind, run_ids, comparison?, options}` → job | `kind` is one of: `figure`, `film`, `slide_loop`, `pptx`, `report`, `spreadsheet` |
| `GET /api/exports/{id}` / `GET /api/exports/{id}/download` | |
| `POST /api/exports/{id}/regenerate` | Rebuilds from the stored run, then reports whether the content is identical (FR-044) |

## Publishing and promo

| Method and path | Purpose |
|---|---|
| `GET/PUT /api/scenarios/{id}/publication` | Edit the PublicationRecord: message, notes, references, sign-off, frame review |
| `GET /api/scenarios/{id}/publication/checklist` | Each item with `ok` and `detail` |
| `POST /api/scenarios/{id}/interactive-data` `{controls}` → job | FR-063 |
| `POST /api/scenarios/{id}/publish` | `publish_refused` naming every missing item, or `{slug, version, path}` |
| `POST /api/published/{slug}/unpublish` `{note}` | Recorded; versions are kept (FR-047) |
| `POST /api/published/{slug}/promo` `{script, approve: true}` → job | Refused unless the scenario is published (FR-041) |
