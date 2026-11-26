-- Revert schemas/media_video/tables/flags_integrity from pg

DROP VIEW IF EXISTS media_video.v_dangling;
DROP TRIGGER IF EXISTS clips_drop_flags    ON media_video.timeline_clips;
DROP TRIGGER IF EXISTS passages_drop_flags ON media_video.passages;
DROP TRIGGER IF EXISTS segments_drop_flags ON media_video.segments;
DROP FUNCTION IF EXISTS media_video.drop_flags_for_subject();
