-- Revert schemas/media_video/tables/laughter from pg

DROP VIEW  IF EXISTS media_video.v_laughter;
DROP TABLE IF EXISTS media_video.laughter CASCADE;
