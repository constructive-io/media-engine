-- Deploy schemas/media_video/tables/words_asr to pg

-- requires: schemas/media_video/tables/transcript

-- A word that has been mended keeps what the recogniser wrote.
--
-- A segment has `text_asr` beside `text` for the same reason: a correction
-- that uses up what it corrected cannot be looked over, cannot be undone, and
-- cannot be told from a word that was right all along. Words are rows of
-- their own, read by everything that cuts on a word's time, so they are
-- mended with their segment and need the same way back.
--
-- Null means the word is as it was heard, which is nearly all of them.
ALTER TABLE media_video.words ADD COLUMN word_asr text;

COMMENT ON COLUMN media_video.words.word_asr IS
  'what the recogniser wrote, where the word has since been mended; null where it has not';
