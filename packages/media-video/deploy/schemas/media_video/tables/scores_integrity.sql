-- Deploy schemas/media_video/tables/scores_integrity to pg

-- requires: schemas/media_video/tables/audio_quality

-- Scores must not outlive what they describe.
--
-- `scores.subject_id` is polymorphic — a passage, a shot, a clip — so Postgres
-- cannot enforce it with a foreign key, and nothing else did either. 51,541 of
-- 101,105 scores referenced passages that had been deleted and re-derived with
-- different boundaries. Half the ledger was describing chunks that no longer
-- existed, and `v_combined` happily ranked them: the top three results were
-- UUIDs with no text behind them.
--
-- That is worse than an empty ledger. An empty one reports nothing; this one
-- reported confidently about material nobody could watch.
--
-- Deterministic chunk ids mean a re-derived chunk KEEPS its scores, which is the
-- accretion the whole design is for. Orphans now only appear when a chunk
-- genuinely ceases to exist — a boundary moved because the transcript improved —
-- and those scores describe something real that is gone, so they go with it.
CREATE OR REPLACE FUNCTION media_video.drop_scores_for_subject()
RETURNS trigger AS $$
BEGIN
  DELETE FROM media_video.scores
   WHERE subject_type = TG_ARGV[0] AND subject_id = OLD.id;
  RETURN OLD;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER passages_drop_scores
  AFTER DELETE ON media_video.passages
  FOR EACH ROW EXECUTE FUNCTION media_video.drop_scores_for_subject('passage');

CREATE TRIGGER shots_drop_scores
  AFTER DELETE ON media_video.shots
  FOR EACH ROW EXECUTE FUNCTION media_video.drop_scores_for_subject('shot');

CREATE TRIGGER clips_drop_scores
  AFTER DELETE ON media_video.timeline_clips
  FOR EACH ROW EXECUTE FUNCTION media_video.drop_scores_for_subject('clip');

-- Clear what accumulated before the triggers existed.
DELETE FROM media_video.scores s
 WHERE (s.subject_type = 'passage'
        AND NOT EXISTS (SELECT 1 FROM media_video.passages p WHERE p.id = s.subject_id))
    OR (s.subject_type = 'shot'
        AND NOT EXISTS (SELECT 1 FROM media_video.shots sh WHERE sh.id = s.subject_id))
    OR (s.subject_type = 'clip'
        AND NOT EXISTS (SELECT 1 FROM media_video.timeline_clips c WHERE c.id = s.subject_id));

-- Same hazard, same fix: an embedding of a passage that no longer exists still
-- answers a search, just about something nobody can cut.
CREATE TRIGGER passages_drop_embeddings
  AFTER DELETE ON media_video.passages
  FOR EACH ROW EXECUTE FUNCTION media_video.drop_scores_for_subject('passage');
