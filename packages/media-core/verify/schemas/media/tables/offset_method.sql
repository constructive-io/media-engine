-- Verify schemas/media/tables/offset_method on pg

DO $$
BEGIN
  ASSERT (SELECT EXISTS (SELECT FROM information_schema.columns
    WHERE table_schema='media' AND table_name='take_assets'
      AND column_name='offset_method')), 'take_assets.offset_method missing';
END $$;
