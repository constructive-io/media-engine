-- Deploy schemas/media_video/tables/flags_integrity to pg

-- requires: schemas/media_video/tables/flags

-- Flags must not outlive what they describe.
--
-- The same hazard as scores, and it recurred for the same reason: `subject_id`
-- is polymorphic, so Postgres cannot enforce it and nothing else did. Deleting
-- 299 empty segments left 11 flags pointing at nothing — suggestions to cut
-- spans of material that no longer exists.
--
-- Generalised this time rather than patched again. Any table holding a
-- polymorphic reference into segments, passages, shots or clips needs the same
-- trigger, and writing it once means the next such table gets it by adding one
-- line instead of by discovering the bug.
CREATE OR REPLACE FUNCTION media_video.drop_flags_for_subject()
RETURNS trigger AS $$
BEGIN
  DELETE FROM media_video.flags
   WHERE subject_type = TG_ARGV[0] AND subject_id = OLD.id;
  RETURN OLD;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER segments_drop_flags
  AFTER DELETE ON media_video.segments
  FOR EACH ROW EXECUTE FUNCTION media_video.drop_flags_for_subject('segment');

CREATE TRIGGER passages_drop_flags
  AFTER DELETE ON media_video.passages
  FOR EACH ROW EXECUTE FUNCTION media_video.drop_flags_for_subject('passage');

CREATE TRIGGER clips_drop_flags
  AFTER DELETE ON media_video.timeline_clips
  FOR EACH ROW EXECUTE FUNCTION media_video.drop_flags_for_subject('clip');

DELETE FROM media_video.flags f
 WHERE (f.subject_type = 'segment'
        AND NOT EXISTS (SELECT 1 FROM media_video.segments s WHERE s.id = f.subject_id))
    OR (f.subject_type = 'passage'
        AND NOT EXISTS (SELECT 1 FROM media_video.passages p WHERE p.id = f.subject_id))
    OR (f.subject_type = 'clip'
        AND NOT EXISTS (SELECT 1 FROM media_video.timeline_clips c WHERE c.id = f.subject_id));

-- Every dangling polymorphic reference in the schema, in one place. A check
-- that has to be remembered is a check that will not be.
CREATE VIEW media_video.v_dangling AS
SELECT 'flags' AS tbl, f.id, f.subject_type, f.subject_id
  FROM media_video.flags f
 WHERE (f.subject_type = 'segment'
        AND NOT EXISTS (SELECT 1 FROM media_video.segments s WHERE s.id = f.subject_id))
    OR (f.subject_type = 'passage'
        AND NOT EXISTS (SELECT 1 FROM media_video.passages p WHERE p.id = f.subject_id))
    OR (f.subject_type = 'clip'
        AND NOT EXISTS (SELECT 1 FROM media_video.timeline_clips c WHERE c.id = f.subject_id))
UNION ALL
SELECT 'scores', s.id, s.subject_type, s.subject_id
  FROM media_video.scores s
 WHERE (s.subject_type = 'passage'
        AND NOT EXISTS (SELECT 1 FROM media_video.passages p WHERE p.id = s.subject_id))
    OR (s.subject_type = 'shot'
        AND NOT EXISTS (SELECT 1 FROM media_video.shots sh WHERE sh.id = s.subject_id))
    OR (s.subject_type = 'clip'
        AND NOT EXISTS (SELECT 1 FROM media_video.timeline_clips c WHERE c.id = s.subject_id));
