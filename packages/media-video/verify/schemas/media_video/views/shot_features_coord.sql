-- Verify schemas/media_video/views/shot_features_coord on pg

DO $$
BEGIN
  ASSERT (SELECT EXISTS (SELECT FROM information_schema.columns
    WHERE table_schema='media_video' AND table_name='v_shot_features'
      AND column_name='coordination')), 'coordination column missing';
END $$;
