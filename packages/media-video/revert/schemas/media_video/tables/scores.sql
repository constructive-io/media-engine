-- Revert schemas/media_video/tables/scores from pg

DROP VIEW  IF EXISTS media_video.v_combined;
DROP TABLE IF EXISTS media_video.score_recipes CASCADE;
DROP TABLE IF EXISTS media_video.scores CASCADE;
