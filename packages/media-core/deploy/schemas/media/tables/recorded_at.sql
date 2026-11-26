-- Deploy schemas/media/tables/recorded_at to pg

-- requires: schemas/media/tables/assets

-- When the camera rolled, in wall-clock time.
--
-- Every device on a shoot writes this somewhere — the recorders put it in the
-- filename (TX02_MIC041_20260813_205140), the cameras put it in a QuickTime
-- creation_time atom — and until now it was thrown away at ingest. That is the
-- one piece of metadata that says which files were recording at the same moment,
-- which is the entire basis for grouping them into a take.
--
-- It is a PRIOR, never an answer. Clocks on a shoot are set by hand and drift,
-- and some cameras stamp the moment the file closed rather than the moment it
-- opened. So this narrows the search — which mics could plausibly belong with
-- this camera — and transcript alignment still supplies the exact offset. Using
-- it as an offset directly is precisely the mistake that makes a sync look
-- almost right.
ALTER TABLE media.assets ADD COLUMN recorded_at timestamptz;
CREATE INDEX assets_recorded_at_idx ON media.assets(recorded_at);
