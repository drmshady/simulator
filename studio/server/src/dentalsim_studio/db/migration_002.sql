-- Schema version 2 (task T071): the required cases each capability's passing report covered, so that adding a
-- required case file returns the capability to awaiting benchmark until a new report passes (FR-062).

ALTER TABLE capability_status ADD COLUMN cases TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(cases));
