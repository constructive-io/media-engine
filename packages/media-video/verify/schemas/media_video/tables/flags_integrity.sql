-- Verify schemas/media_video/tables/flags_integrity on pg

SELECT 'media_video.v_dangling'::regclass;
DO $$
BEGIN
  ASSERT (SELECT count(*) FROM media_video.v_dangling) = 0,
    'dangling polymorphic references remain';
END $$;
