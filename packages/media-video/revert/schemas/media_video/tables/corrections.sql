-- Revert schemas/media_video/tables/corrections from pg

DROP VIEW  IF EXISTS media_video.v_corrections;
DROP TABLE IF EXISTS media_video.corrections CASCADE;
ALTER TABLE media_video.segments DROP COLUMN IF EXISTS text_asr;
