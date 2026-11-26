-- Revert schemas/media_audio/indexes/foreign_keys from pg

DROP INDEX IF EXISTS media_audio.review_queue_render_id_fk_idx;
DROP INDEX IF EXISTS media_audio.review_queue_persona_id_fk_idx;
DROP INDEX IF EXISTS media_audio.renders_asset_id_fk_idx;
DROP INDEX IF EXISTS media_audio.render_chunks_persona_id_fk_idx;
DROP INDEX IF EXISTS media_audio.render_chunks_emotion_fk_idx;
