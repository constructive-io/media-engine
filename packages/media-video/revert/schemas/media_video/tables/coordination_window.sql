-- Revert schemas/media_video/tables/coordination_window from pg

ALTER TABLE media_video.coordination DROP COLUMN IF EXISTS best_at_s;
