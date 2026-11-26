-- Revert schemas/media_video/tables/passages from pg

ALTER TABLE media_video.decisions DROP CONSTRAINT IF EXISTS decisions_kind_check;
ALTER TABLE media_video.decisions ADD CONSTRAINT decisions_kind_check
  CHECK (kind IN ('shot_pref','shot_keep','segment_keep','cut_point'));
ALTER TABLE media_video.decisions DROP COLUMN IF EXISTS rejected_passage;
ALTER TABLE media_video.decisions DROP COLUMN IF EXISTS passage_id;
DROP TABLE IF EXISTS media_video.passages CASCADE;
