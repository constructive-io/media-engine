-- Verify schemas/media_video/indexes/redundant on pg

DO $$
BEGIN
  ASSERT NOT EXISTS (
    SELECT FROM pg_indexes
    WHERE schemaname = 'media_video'
      AND indexname IN ('frames_shot_idx', 'laughter_asset_idx', 'scores_subject_idx',
                        'segments_asset_idx', 'shots_asset_idx', 'timeline_clips_timeline_idx',
                        'vocabulary_project_idx', 'words_segment_idx')
  ), 'a redundant index is still present';
END $$;
