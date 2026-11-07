-- Schema version 3 (tasks T114 and T121): notes and tags on runs, the triggers that keep the search index (FR-031) up
-- to date, and the backup and restore job kinds (FR-034).

ALTER TABLE runs ADD COLUMN notes TEXT NOT NULL DEFAULT '';

ALTER TABLE runs ADD COLUMN tags TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(tags));

-- Search index triggers: one row per project, scenario and run; tags are indexed as space-separated words. A run's
-- name is its snapshot's name, which never changes, so only its notes and tags are watched.

CREATE TRIGGER projects_search_insert AFTER INSERT ON projects BEGIN
    INSERT INTO search_index (kind, item_id, name, notes, tags)
    VALUES ('project', NEW.id, NEW.name, NEW.notes,
            (SELECT coalesce(group_concat(value, ' '), '') FROM json_each(NEW.tags)));
END;

CREATE TRIGGER projects_search_update AFTER UPDATE OF name, notes, tags ON projects BEGIN
    DELETE FROM search_index WHERE kind = 'project' AND item_id = OLD.id;
    INSERT INTO search_index (kind, item_id, name, notes, tags)
    VALUES ('project', NEW.id, NEW.name, NEW.notes,
            (SELECT coalesce(group_concat(value, ' '), '') FROM json_each(NEW.tags)));
END;

CREATE TRIGGER projects_search_delete AFTER DELETE ON projects BEGIN
    DELETE FROM search_index WHERE kind = 'project' AND item_id = OLD.id;
END;

CREATE TRIGGER scenarios_search_insert AFTER INSERT ON scenarios BEGIN
    INSERT INTO search_index (kind, item_id, name, notes, tags)
    VALUES ('scenario', NEW.id, NEW.name, NEW.notes,
            (SELECT coalesce(group_concat(value, ' '), '') FROM json_each(NEW.tags)));
END;

CREATE TRIGGER scenarios_search_update AFTER UPDATE OF name, notes, tags ON scenarios BEGIN
    DELETE FROM search_index WHERE kind = 'scenario' AND item_id = OLD.id;
    INSERT INTO search_index (kind, item_id, name, notes, tags)
    VALUES ('scenario', NEW.id, NEW.name, NEW.notes,
            (SELECT coalesce(group_concat(value, ' '), '') FROM json_each(NEW.tags)));
END;

CREATE TRIGGER scenarios_search_delete AFTER DELETE ON scenarios BEGIN
    DELETE FROM search_index WHERE kind = 'scenario' AND item_id = OLD.id;
END;

CREATE TRIGGER runs_search_insert AFTER INSERT ON runs BEGIN
    INSERT INTO search_index (kind, item_id, name, notes, tags)
    VALUES ('run', NEW.id, coalesce(json_extract(NEW.snapshot, '$.name'), ''), NEW.notes,
            (SELECT coalesce(group_concat(value, ' '), '') FROM json_each(NEW.tags)));
END;

CREATE TRIGGER runs_search_update AFTER UPDATE OF notes, tags ON runs BEGIN
    DELETE FROM search_index WHERE kind = 'run' AND item_id = OLD.id;
    INSERT INTO search_index (kind, item_id, name, notes, tags)
    VALUES ('run', NEW.id, coalesce(json_extract(NEW.snapshot, '$.name'), ''), NEW.notes,
            (SELECT coalesce(group_concat(value, ' '), '') FROM json_each(NEW.tags)));
END;

CREATE TRIGGER runs_search_delete AFTER DELETE ON runs BEGIN
    DELETE FROM search_index WHERE kind = 'run' AND item_id = OLD.id;
END;

-- Index what is already stored.

DELETE FROM search_index;

INSERT INTO search_index (kind, item_id, name, notes, tags)
SELECT 'project', id, name, notes, (SELECT coalesce(group_concat(value, ' '), '') FROM json_each(projects.tags))
FROM projects;

INSERT INTO search_index (kind, item_id, name, notes, tags)
SELECT 'scenario', id, name, notes, (SELECT coalesce(group_concat(value, ' '), '') FROM json_each(scenarios.tags))
FROM scenarios;

INSERT INTO search_index (kind, item_id, name, notes, tags)
SELECT 'run', id, coalesce(json_extract(snapshot, '$.name'), ''), notes,
       (SELECT coalesce(group_concat(value, ' '), '') FROM json_each(runs.tags))
FROM runs;

-- The job kinds gain backup and restore. SQLite cannot change a CHECK constraint, so the table is rebuilt.

CREATE TABLE jobs_v3 (
    id               TEXT PRIMARY KEY,
    lane             TEXT NOT NULL CHECK (lane IN ('heavy', 'preview')),
    kind             TEXT NOT NULL
                     CHECK (kind IN ('run', 'preview', 'mesh_check', 'verification', 'export', 'promo',
                                     'interactive_data', 'backup', 'restore')),
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

INSERT INTO jobs_v3 SELECT id, lane, kind, payload, target_id, position, status, pid, heartbeat_at, attempts,
                           failure_reason, cancel_requested, created_at, started_at, finished_at
FROM jobs;

DROP TABLE jobs;

ALTER TABLE jobs_v3 RENAME TO jobs;

CREATE INDEX jobs_lane_queue ON jobs (lane, status, position);
