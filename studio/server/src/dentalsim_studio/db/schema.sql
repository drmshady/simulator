-- Studio database, schema version 1 (data-model.md, research R12).
--
-- Conventions:
--   - ids are ULID strings; times are UTC ISO 8601 text
--   - JSON columns hold the engine's pydantic models serialised with model_dump(mode="json")
--   - no table has a field for patient identity or patient data (FR-048)

CREATE TABLE settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL CHECK (json_valid(value)),
    updated_at TEXT NOT NULL
);

-- Projects, scenarios, templates --------------------------------------------------------------------------------

CREATE TABLE projects (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    notes      TEXT NOT NULL DEFAULT '',
    tags       TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(tags)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE scenarios (
    id           TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL REFERENCES projects (id) ON DELETE RESTRICT,
    name         TEXT NOT NULL,
    notes        TEXT NOT NULL DEFAULT '',
    tags         TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(tags)),
    derived_from TEXT CHECK (derived_from IS NULL OR json_valid(derived_from)),
    definition   TEXT NOT NULL CHECK (json_valid(definition)),
    revision     INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    validity     TEXT CHECK (validity IS NULL OR json_valid(validity)),
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX scenarios_project ON scenarios (project_id);

CREATE TABLE owner_templates (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    prosthesis_type TEXT NOT NULL,
    branch          TEXT NOT NULL CHECK (branch IN ('implant', 'tooth', 'removable')),
    definition      TEXT NOT NULL CHECK (json_valid(definition)),
    created_at      TEXT NOT NULL
);

-- Studies, runs, previews ----------------------------------------------------------------------------------------

CREATE TABLE studies (
    id               TEXT PRIMARY KEY,
    project_id       TEXT NOT NULL REFERENCES projects (id) ON DELETE RESTRICT,
    base_scenario_id TEXT NOT NULL REFERENCES scenarios (id) ON DELETE RESTRICT,
    kind             TEXT NOT NULL CHECK (kind IN ('sweep', 'sensitivity')),
    axes             TEXT NOT NULL CHECK (json_valid(axes)),
    status           TEXT NOT NULL DEFAULT 'queued'
                     CHECK (status IN ('queued', 'running', 'finished', 'finished_with_failures', 'cancelled')),
    summary          TEXT CHECK (summary IS NULL OR json_valid(summary)),
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE runs (
    id                  TEXT PRIMARY KEY,
    scenario_id         TEXT REFERENCES scenarios (id) ON DELETE RESTRICT,
    scenario_revision   INTEGER,
    snapshot            TEXT NOT NULL CHECK (json_valid(snapshot)),
    detail_level        TEXT NOT NULL CHECK (detail_level IN ('preview', 'standard', 'fine', 'extra_fine')),
    input_hash          TEXT NOT NULL,
    library_versions    TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(library_versions)),
    engine_version      TEXT NOT NULL,
    engine_source_hash  TEXT NOT NULL,
    conventions_version TEXT NOT NULL,
    solver_build_id     TEXT NOT NULL,
    gmsh_version        TEXT NOT NULL,
    solver_host         TEXT,
    estimate            TEXT CHECK (estimate IS NULL OR json_valid(estimate)),
    actual              TEXT CHECK (actual IS NULL OR json_valid(actual)),
    status              TEXT NOT NULL DEFAULT 'queued'
                        CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted')),
    failure_reason      TEXT,
    convergence         TEXT CHECK (convergence IS NULL OR json_valid(convergence)),
    result_ref          TEXT CHECK (result_ref IS NULL OR status = 'succeeded'),
    locked_results      TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(locked_results)),
    flags               TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(flags)),
    study_id            TEXT REFERENCES studies (id) ON DELETE SET NULL,
    created_at          TEXT NOT NULL,
    started_at          TEXT,
    finished_at         TEXT
);
CREATE INDEX runs_scenario ON runs (scenario_id);
CREATE INDEX runs_study ON runs (study_id);
CREATE INDEX runs_input_hash ON runs (input_hash);

-- Which library entry versions each run used, so rejecting a version can flag its runs (FR-055).
CREATE TABLE run_library_versions (
    run_id   TEXT NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    entry_id TEXT NOT NULL REFERENCES library_entries (id) ON DELETE RESTRICT,
    version  INTEGER NOT NULL,
    PRIMARY KEY (run_id, entry_id, version),
    FOREIGN KEY (entry_id, version) REFERENCES library_versions (entry_id, version) ON DELETE RESTRICT
);
CREATE INDEX run_library_versions_entry ON run_library_versions (entry_id, version);

CREATE TABLE previews (
    id                TEXT PRIMARY KEY,
    input_hash        TEXT NOT NULL,
    scenario_id       TEXT REFERENCES scenarios (id) ON DELETE CASCADE,
    scenario_revision INTEGER,
    status            TEXT NOT NULL DEFAULT 'queued'
                      CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted')),
    failure_reason    TEXT,
    result_ref        TEXT,
    accuracy_note     TEXT,
    created_at        TEXT NOT NULL
);
CREATE INDEX previews_input_hash ON previews (input_hash);
CREATE INDEX previews_scenario ON previews (scenario_id);

-- Queue and events -----------------------------------------------------------------------------------------------

CREATE TABLE jobs (
    id               TEXT PRIMARY KEY,
    lane             TEXT NOT NULL CHECK (lane IN ('heavy', 'preview')),
    kind             TEXT NOT NULL
                     CHECK (kind IN ('run', 'preview', 'mesh_check', 'verification', 'export', 'promo',
                                     'interactive_data')),
    payload          TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(payload)),
    target_id        TEXT,
    position         INTEGER NOT NULL,
    status           TEXT NOT NULL DEFAULT 'queued'
                     CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted')),
    pid              INTEGER,
    heartbeat_at     TEXT,
    attempts         INTEGER NOT NULL DEFAULT 0,
    failure_reason   TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)),
    created_at       TEXT NOT NULL,
    started_at       TEXT,
    finished_at      TEXT
);
CREATE INDEX jobs_lane_queue ON jobs (lane, status, position);

CREATE TABLE events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    type       TEXT NOT NULL,
    payload    TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(payload)),
    created_at TEXT NOT NULL
);

-- Literature library ---------------------------------------------------------------------------------------------

CREATE TABLE library_entries (
    id              TEXT PRIMARY KEY,
    parameter_key   TEXT NOT NULL UNIQUE,
    category        TEXT NOT NULL CHECK (category IN ('material', 'bone', 'load', 'strength', 'frost_window')),
    label           TEXT NOT NULL,
    current_version INTEGER,
    created_at      TEXT NOT NULL
);

CREATE TABLE library_versions (
    entry_id        TEXT NOT NULL REFERENCES library_entries (id) ON DELETE RESTRICT,
    version         INTEGER NOT NULL CHECK (version >= 1),
    units           TEXT NOT NULL,
    typical         REAL NOT NULL,
    range_min       REAL NOT NULL,
    range_max       REAL NOT NULL,
    notes           TEXT,
    status          TEXT NOT NULL DEFAULT 'proposed'
                    CHECK (status IN ('proposed', 'approved', 'rejected', 'superseded')),
    origin          TEXT NOT NULL CHECK (origin IN ('claude_proposal', 'owner')),
    proposed_at     TEXT NOT NULL,
    approved_at     TEXT,
    rejected_at     TEXT,
    rejected_reason TEXT,
    superseded_at   TEXT,
    content_hash    TEXT NOT NULL,
    PRIMARY KEY (entry_id, version),
    CHECK (range_min <= typical AND typical <= range_max)
);

CREATE TABLE sources (
    id           TEXT PRIMARY KEY,
    authors      TEXT NOT NULL CHECK (json_valid(authors)),
    year         INTEGER NOT NULL,
    title        TEXT NOT NULL,
    journal      TEXT,
    doi          TEXT,
    pmid         TEXT,
    study_type   TEXT NOT NULL
                 CHECK (study_type IN ('experimental', 'clinical', 'fea', 'review', 'standard', 'textbook')),
    check_status TEXT NOT NULL DEFAULT 'not_checked'
                 CHECK (check_status IN ('not_checked', 'resolved_match', 'resolved_mismatch', 'unresolved')),
    check_result TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(check_result)),
    created_at   TEXT NOT NULL,
    CHECK (doi IS NOT NULL OR pmid IS NOT NULL)
);
CREATE UNIQUE INDEX sources_doi ON sources (lower(doi)) WHERE doi IS NOT NULL;
CREATE UNIQUE INDEX sources_pmid ON sources (pmid) WHERE pmid IS NOT NULL;

CREATE TABLE version_sources (
    entry_id  TEXT NOT NULL,
    version   INTEGER NOT NULL,
    source_id TEXT NOT NULL REFERENCES sources (id) ON DELETE RESTRICT,
    position  INTEGER NOT NULL,
    value     REAL NOT NULL,
    note      TEXT,
    PRIMARY KEY (entry_id, version, source_id),
    FOREIGN KEY (entry_id, version) REFERENCES library_versions (entry_id, version) ON DELETE CASCADE
);
CREATE INDEX version_sources_source ON version_sources (source_id);

-- Physics capabilities and verification --------------------------------------------------------------------------

CREATE TABLE capability_status (
    capability_id TEXT PRIMARY KEY,
    status        TEXT NOT NULL DEFAULT 'awaiting_benchmark' CHECK (status IN ('awaiting_benchmark', 'ready')),
    report_id     TEXT REFERENCES verification_reports (id) ON DELETE SET NULL,
    updated_at    TEXT NOT NULL,
    CHECK (status = 'awaiting_benchmark' OR report_id IS NOT NULL)
);

CREATE TABLE verification_reports (
    id                  TEXT PRIMARY KEY,
    engine_version      TEXT NOT NULL,
    solver_build_id     TEXT NOT NULL,
    conventions_version TEXT NOT NULL,
    overall             TEXT NOT NULL CHECK (overall IN ('pass', 'fail')),
    report              TEXT NOT NULL CHECK (json_valid(report)),
    created_at          TEXT NOT NULL
);

CREATE TABLE mesh_checks (
    id                 TEXT PRIMARY KEY,
    scenario_id        TEXT NOT NULL REFERENCES scenarios (id) ON DELETE CASCADE,
    levels             TEXT NOT NULL CHECK (json_valid(levels)),
    run_ids            TEXT NOT NULL CHECK (json_valid(run_ids)),
    key_number_changes TEXT NOT NULL CHECK (json_valid(key_number_changes)),
    limit_value        REAL NOT NULL,
    pass               INTEGER NOT NULL CHECK (pass IN (0, 1)),
    created_at         TEXT NOT NULL
);

-- Publishing, exports, promo -------------------------------------------------------------------------------------

CREATE TABLE publication_records (
    scenario_id TEXT PRIMARY KEY REFERENCES scenarios (id) ON DELETE RESTRICT,
    record      TEXT NOT NULL CHECK (json_valid(record)),
    updated_at  TEXT NOT NULL
);

CREATE TABLE published_scenarios (
    slug        TEXT PRIMARY KEY,
    scenario_id TEXT NOT NULL REFERENCES scenarios (id) ON DELETE RESTRICT,
    state       TEXT NOT NULL CHECK (state IN ('published', 'unpublished')),
    flags       TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(flags)),
    history     TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(history)),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE published_versions (
    slug         TEXT NOT NULL REFERENCES published_scenarios (slug) ON DELETE RESTRICT,
    version      INTEGER NOT NULL CHECK (version >= 1),
    path         TEXT NOT NULL,
    published_at TEXT NOT NULL,
    package_hash TEXT NOT NULL,
    PRIMARY KEY (slug, version)
);

CREATE TABLE exports (
    id               TEXT PRIMARY KEY,
    kind             TEXT NOT NULL
                     CHECK (kind IN ('figure', 'film', 'slide_loop', 'pptx', 'report', 'spreadsheet', 'promo',
                                     'interactive_data')),
    source           TEXT NOT NULL CHECK (json_valid(source)),
    template_version TEXT NOT NULL,
    options          TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(options)),
    output_ref       TEXT,
    created_at       TEXT NOT NULL
);

CREATE TABLE promo_clips (
    id                TEXT PRIMARY KEY,
    published_slug    TEXT NOT NULL REFERENCES published_scenarios (slug) ON DELETE RESTRICT,
    published_version INTEGER NOT NULL,
    script            TEXT NOT NULL CHECK (json_valid(script)),
    voice             TEXT NOT NULL CHECK (json_valid(voice)),
    duration_s        REAL NOT NULL CHECK (duration_s BETWEEN 15 AND 45),
    media_ref         TEXT,
    created_at        TEXT NOT NULL
);

-- Search (FR-031): names, notes and tags of projects, scenarios and runs. Kept up to date by triggers (T114).

CREATE VIRTUAL TABLE search_index USING fts5 (
    kind UNINDEXED,
    item_id UNINDEXED,
    name,
    notes,
    tags,
    tokenize = 'unicode61 remove_diacritics 2'
);
