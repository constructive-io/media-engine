-- Verify schemas/media/indexes/foreign_keys on pg

SELECT 'media.episode_assets_asset_id_fk_idx'::regclass;
SELECT 'media.episode_takes_take_id_fk_idx'::regclass;
SELECT 'media.take_assets_persona_id_fk_idx'::regclass;
SELECT 'media.voices_sample_asset_id_fk_idx'::regclass;
