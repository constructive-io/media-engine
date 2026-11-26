-- Revert schemas/media_video/tables/passage_quality from pg

DROP VIEW IF EXISTS media_video.v_passages;
DROP TABLE IF EXISTS media_video.passage_quality CASCADE;
