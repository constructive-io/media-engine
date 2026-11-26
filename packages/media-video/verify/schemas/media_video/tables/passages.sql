-- Verify schemas/media_video/tables/passages on pg

SELECT 'media_video.passages'::regclass;
DO $$
BEGIN
  ASSERT (SELECT EXISTS (SELECT FROM information_schema.columns
    WHERE table_schema='media_video' AND table_name='decisions'
      AND column_name='passage_id')), 'decisions.passage_id missing';
END $$;
