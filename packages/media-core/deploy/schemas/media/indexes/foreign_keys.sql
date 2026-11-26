-- Deploy schemas/media/indexes/foreign_keys to pg

-- requires: schemas/media/tables/geometry

-- Every foreign key leads an index, so deleting a parent row or joining across
-- the key does not scan the child table.
CREATE INDEX episode_assets_asset_id_fk_idx ON media.episode_assets (asset_id);
CREATE INDEX episode_takes_take_id_fk_idx ON media.episode_takes (take_id);
CREATE INDEX take_assets_persona_id_fk_idx ON media.take_assets (persona_id);
CREATE INDEX voices_sample_asset_id_fk_idx ON media.voices (sample_asset_id);
