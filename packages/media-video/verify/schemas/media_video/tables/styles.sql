-- Verify schemas/media_video/tables/styles on pg

SELECT 'media_video.styles'::regclass;
SELECT 'media_video.v_runs'::regclass;
DO $$
BEGIN
  ASSERT (SELECT count(*) FROM media_video.styles) >= 2, 'styles not seeded';
END $$;
