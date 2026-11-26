-- Revert schemas/media/tables/recorded_at from pg

DROP INDEX IF EXISTS media.assets_recorded_at_idx;
ALTER TABLE media.assets DROP COLUMN IF EXISTS recorded_at;
