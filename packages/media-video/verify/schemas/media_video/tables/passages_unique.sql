-- Verify schemas/media_video/tables/passages_unique on pg

DO $$
BEGIN
  ASSERT (SELECT EXISTS (SELECT FROM pg_indexes WHERE schemaname='media_video'
            AND indexname='passages_span_key')), 'passages_span_key missing';
END $$;
