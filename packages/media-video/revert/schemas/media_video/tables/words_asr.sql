-- Revert schemas/media_video/tables/words_asr from pg

ALTER TABLE media_video.words DROP COLUMN IF EXISTS word_asr;
