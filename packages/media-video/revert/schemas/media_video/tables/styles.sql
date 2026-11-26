-- Revert schemas/media_video/tables/styles from pg

DROP VIEW IF EXISTS media_video.v_runs;
DROP INDEX IF EXISTS media_video.timelines_style_idx;
ALTER TABLE media_video.selectors DROP COLUMN IF EXISTS style_id;
ALTER TABLE media_video.timelines DROP COLUMN IF EXISTS run_at;
ALTER TABLE media_video.timelines DROP COLUMN IF EXISTS style_id;
DROP TABLE IF EXISTS media_video.styles CASCADE;
