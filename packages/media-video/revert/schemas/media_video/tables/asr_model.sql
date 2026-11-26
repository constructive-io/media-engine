-- Revert schemas/media_video/tables/asr_model from pg

DROP VIEW  IF EXISTS media_video.v_asr_quality;
DROP INDEX IF EXISTS media_video.segments_model_idx;
ALTER TABLE media_video.segments DROP COLUMN IF EXISTS model;
