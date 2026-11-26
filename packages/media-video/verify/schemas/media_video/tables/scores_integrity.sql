-- Verify schemas/media_video/tables/scores_integrity on pg

DO $$
BEGIN
  ASSERT (SELECT count(*) FROM pg_trigger
           WHERE tgname = 'passages_drop_scores') = 1, 'trigger missing';
  ASSERT (SELECT count(*) FROM media_video.scores s
           WHERE s.subject_type = 'passage'
             AND NOT EXISTS (SELECT 1 FROM media_video.passages p
                              WHERE p.id = s.subject_id)) = 0,
    'orphan passage scores remain';
END $$;
