-- Schema version 4 (task T124): what a study needs beyond schema.sql's table: its name and detail level, its runs in
-- queue order with the axis values that made each one (`points`), and the whole study's estimate when it was queued
-- (FR-057).

ALTER TABLE studies ADD COLUMN name TEXT NOT NULL DEFAULT '';

ALTER TABLE studies ADD COLUMN detail_level TEXT NOT NULL DEFAULT 'standard'
    CHECK (detail_level IN ('standard', 'fine', 'extra_fine'));

ALTER TABLE studies ADD COLUMN points TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(points));

ALTER TABLE studies ADD COLUMN estimate TEXT CHECK (estimate IS NULL OR json_valid(estimate));

CREATE INDEX studies_project ON studies (project_id);
