-- Verify schemas/media/tables/recorded_at on pg

DO $$
BEGIN
  ASSERT (SELECT EXISTS (SELECT FROM information_schema.columns
    WHERE table_schema='media' AND table_name='assets'
      AND column_name='recorded_at')), 'assets.recorded_at missing';
END $$;
