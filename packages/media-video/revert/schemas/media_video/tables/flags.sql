-- Revert schemas/media_video/tables/flags from pg

DROP VIEW  IF EXISTS media_video.v_timeline_flags;
DROP TABLE IF EXISTS media_video.flags CASCADE;
