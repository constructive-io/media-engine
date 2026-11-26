-- Revert schemas/media_video/tables/variants from pg

DROP VIEW  IF EXISTS media_video.v_timeline_review;
DROP VIEW  IF EXISTS media_video.v_excluded_shots;
DROP TABLE IF EXISTS media_video.exclusions CASCADE;
DROP TABLE IF EXISTS media_video.clip_notes CASCADE;
DROP INDEX IF EXISTS media_video.timelines_episode_idx;
ALTER TABLE media_video.timelines
  DROP COLUMN IF EXISTS status,
  DROP COLUMN IF EXISTS seed,
  DROP COLUMN IF EXISTS revision,
  DROP COLUMN IF EXISTS parent_id,
  DROP COLUMN IF EXISTS selector_id,
  DROP COLUMN IF EXISTS episode_id;
