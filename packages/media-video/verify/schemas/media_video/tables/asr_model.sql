-- Verify schemas/media_video/tables/asr_model on pg

SELECT 'media_video.v_asr_quality'::regclass;
DO $$
BEGIN
  ASSERT (SELECT count(*) FROM media_video.segments WHERE model IS NULL) = 0,
    'segments exist with no recorded model';
END $$;
