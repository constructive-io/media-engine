-- Revert schemas/media_video/tables/generations_medium from pg

DROP VIEW IF EXISTS media_video.v_generation_chain;
DROP INDEX IF EXISTS media_video.generations_medium_idx;
ALTER TABLE media_video.generations DROP CONSTRAINT IF EXISTS generations_medium_check;
ALTER TABLE media_video.generations DROP COLUMN IF EXISTS medium;
