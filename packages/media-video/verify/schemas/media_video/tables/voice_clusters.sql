-- Verify schemas/media_video/tables/voice_clusters on pg

SELECT model, centroid, cohesion, n_windows, speech_s
  FROM media_video.speaker_clusters
 WHERE false;

DO $$
BEGIN
  ASSERT (SELECT column_default IS NULL FROM information_schema.columns
           WHERE table_schema = 'media_video' AND table_name = 'speaker_clusters'
             AND column_name = 'method'),
    'media_video.speaker_clusters.method still has a default';
END $$;
