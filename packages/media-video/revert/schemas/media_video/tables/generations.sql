-- Revert schemas/media_video/tables/generations from pg

DROP VIEW  IF EXISTS media_video.v_generation_spend;
DROP VIEW  IF EXISTS media_video.v_synthetic_in_timeline;
DROP VIEW  IF EXISTS media_video.v_synthetic_assets;
DROP TABLE IF EXISTS media_video.generations CASCADE;
