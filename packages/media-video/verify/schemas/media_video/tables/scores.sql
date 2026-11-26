-- Verify schemas/media_video/tables/scores on pg

SELECT 'media_video.scores'::regclass;
SELECT 'media_video.score_recipes'::regclass;
SELECT 'media_video.v_combined'::regclass;
DO $$
BEGIN
  ASSERT (SELECT count(*) FROM media_video.score_recipes) >= 3, 'recipes missing';
END $$;
