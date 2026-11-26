-- Revert schemas/media/tables/geometry from pg

DROP INDEX IF EXISTS media.assets_aspect_idx;
ALTER TABLE media.assets DROP COLUMN IF EXISTS orientation;
ALTER TABLE media.assets DROP COLUMN IF EXISTS aspect;
