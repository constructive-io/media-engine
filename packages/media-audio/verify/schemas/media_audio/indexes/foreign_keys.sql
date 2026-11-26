-- Verify schemas/media_audio/indexes/foreign_keys on pg

SELECT 'media_audio.render_chunks_emotion_fk_idx'::regclass;
SELECT 'media_audio.render_chunks_persona_id_fk_idx'::regclass;
SELECT 'media_audio.renders_asset_id_fk_idx'::regclass;
SELECT 'media_audio.review_queue_persona_id_fk_idx'::regclass;
SELECT 'media_audio.review_queue_render_id_fk_idx'::regclass;
