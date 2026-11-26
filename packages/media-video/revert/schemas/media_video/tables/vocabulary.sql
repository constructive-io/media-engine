-- Revert schemas/media_video/tables/vocabulary from pg

ALTER TABLE media_video.segments
  DROP COLUMN IF EXISTS compression_ratio,
  DROP COLUMN IF EXISTS no_speech_prob,
  DROP COLUMN IF EXISTS avg_logprob;
DROP TABLE IF EXISTS media_video.vocabulary CASCADE;
