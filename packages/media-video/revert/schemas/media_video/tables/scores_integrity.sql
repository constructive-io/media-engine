-- Revert schemas/media_video/tables/scores_integrity from pg

DROP TRIGGER IF EXISTS passages_drop_embeddings ON media_video.passages;
DROP TRIGGER IF EXISTS clips_drop_scores    ON media_video.timeline_clips;
DROP TRIGGER IF EXISTS shots_drop_scores    ON media_video.shots;
DROP TRIGGER IF EXISTS passages_drop_scores ON media_video.passages;
DROP FUNCTION IF EXISTS media_video.drop_scores_for_subject();
