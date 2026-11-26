-- Verify schemas/media/tables/geometry on pg

DO $$
BEGIN
  ASSERT (SELECT count(*) FROM media.assets
           WHERE medium='video' AND width IS NOT NULL AND aspect IS NULL) = 0,
    'video assets exist with no computed aspect';
END $$;
