-- Verify schemas/media_video/tables/words_asr on pg

DO $$
BEGIN
  ASSERT (SELECT EXISTS (SELECT FROM information_schema.columns
    WHERE table_schema='media_video' AND table_name='words'
      AND column_name='word_asr')), 'words.word_asr missing';
END $$;
