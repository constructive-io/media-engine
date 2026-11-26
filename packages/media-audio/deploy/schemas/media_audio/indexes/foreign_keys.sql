-- Deploy schemas/media_audio/indexes/foreign_keys to pg

-- requires: schemas/media_audio/tables/review

-- Every foreign key leads an index, so deleting a parent row or joining across
-- the key does not scan the child table.
CREATE INDEX render_chunks_emotion_fk_idx ON media_audio.render_chunks (emotion);
CREATE INDEX render_chunks_persona_id_fk_idx ON media_audio.render_chunks (persona_id);
CREATE INDEX renders_asset_id_fk_idx ON media_audio.renders (asset_id);
CREATE INDEX review_queue_persona_id_fk_idx ON media_audio.review_queue (persona_id);
CREATE INDEX review_queue_render_id_fk_idx ON media_audio.review_queue (render_id);
