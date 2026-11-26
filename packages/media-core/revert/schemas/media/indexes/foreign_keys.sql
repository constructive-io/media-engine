-- Revert schemas/media/indexes/foreign_keys from pg

DROP INDEX IF EXISTS media.voices_sample_asset_id_fk_idx;
DROP INDEX IF EXISTS media.take_assets_persona_id_fk_idx;
DROP INDEX IF EXISTS media.episode_takes_take_id_fk_idx;
DROP INDEX IF EXISTS media.episode_assets_asset_id_fk_idx;
