-- Revert schemas/media_video/tables/audio_quality from pg

DROP VIEW  IF EXISTS media_video.v_audible;
DROP TABLE IF EXISTS media_video.audio_quality CASCADE;
