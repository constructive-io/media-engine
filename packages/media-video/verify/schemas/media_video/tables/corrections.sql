-- Verify schemas/media_video/tables/corrections on pg

SELECT 'media_video.corrections'::regclass;
DO $$
BEGIN
  ASSERT (SELECT count(*) FROM media_video.segments WHERE text_asr IS NULL) = 0,
    'segments exist with no recorded ASR text';
END $$;
