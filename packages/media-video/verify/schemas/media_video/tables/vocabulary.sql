-- Verify schemas/media_video/tables/vocabulary on pg

SELECT 'media_video.vocabulary'::regclass;
DO $$
BEGIN
  ASSERT (SELECT EXISTS (SELECT FROM information_schema.columns
    WHERE table_schema='media_video' AND table_name='segments'
      AND column_name='avg_logprob')), 'segments.avg_logprob missing';
END $$;
