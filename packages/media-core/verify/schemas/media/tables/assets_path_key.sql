-- Verify schemas/media/tables/assets_path_key on pg

DO $$
BEGIN
  ASSERT (SELECT EXISTS (
    SELECT FROM pg_indexes
     WHERE schemaname = 'media' AND indexname = 'assets_path_key'
  )), 'index media.assets_path_key does not exist';
END $$;
