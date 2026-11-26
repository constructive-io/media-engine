-- Revert schemas/media_video/tables/recipes from pg

DROP TABLE IF EXISTS media_video.selectors CASCADE;
DROP TABLE IF EXISTS media_video.models CASCADE;
