-- Revert schemas/media_video/tables/timelines from pg

DROP VIEW IF EXISTS media_video.v_timeline;
DROP TABLE IF EXISTS media_video.timeline_clips;
DROP TABLE IF EXISTS media_video.timelines;
