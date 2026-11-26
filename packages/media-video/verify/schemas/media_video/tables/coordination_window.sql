-- Verify schemas/media_video/tables/coordination_window on pg

DO $$
BEGIN
  ASSERT (SELECT EXISTS (SELECT FROM information_schema.columns
    WHERE table_schema='media_video' AND table_name='coordination'
      AND column_name='best_at_s')), 'best_at_s missing';
END $$;
