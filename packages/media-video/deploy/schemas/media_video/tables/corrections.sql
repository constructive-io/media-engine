-- Deploy schemas/media_video/tables/corrections to pg

-- requires: schemas/media_video/tables/embeddings

-- Corrections are a LAYER, never an overwrite.
--
-- A repair once rewrote segments.text in place to fix eleven mishearings. That
-- text is the raw extract — what the recogniser actually said — and once
-- overwritten there is no way back except re-running ASR. A correction that
-- destroys its input cannot be reviewed, cannot be reverted, and cannot be
-- distinguished from a transcription that was always right.
--
-- The rule this restores: NOTHING in the pipeline edits its own source. Media
-- files are never touched, and the first derived layer — the transcript — is now
-- equally immutable. Everything above it is a stack of suggestions that can be
-- dropped and rebuilt, which is what makes any of it safe to automate.
--
--   text_asr    what the recogniser said. Written once, never updated.
--   corrections one row per change, with the rule that made it
--   text        the current best reading, derived from the two
--
-- Restoring the raw is `UPDATE segments SET text = text_asr`. Auditing a
-- correction is a SELECT. Neither is possible when the fix is applied in place.
ALTER TABLE media_video.segments ADD COLUMN text_asr text;

-- Backfill: for everything not yet corrected, the current text IS the raw
-- extract, so this is exact rather than a guess. The eleven segments repaired
-- before this existed are the exception and are re-derived from audio.
UPDATE media_video.segments SET text_asr = text WHERE text_asr IS NULL;

CREATE TABLE media_video.corrections (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  segment_id  uuid NOT NULL REFERENCES media_video.segments(id) ON DELETE CASCADE,
  -- What it was before this correction, so a chain of them is walkable.
  before_text text NOT NULL,
  after_text  text NOT NULL,
  -- Which vocabulary rule fired, so a bad rule can be found and its every
  -- effect reversed rather than hunted.
  rule        text,
  actor       text NOT NULL DEFAULT 'repair',
  applied_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX corrections_segment_idx ON media_video.corrections(segment_id);
CREATE INDEX corrections_rule_idx    ON media_video.corrections(rule);

-- What changed, and what it changed from.
CREATE VIEW media_video.v_corrections AS
SELECT c.id, c.rule, c.actor, c.applied_at,
       substring(a.path from '[^/]+$') AS file,
       s.start_s, c.before_text, c.after_text,
       s.text_asr AS original_asr
  FROM media_video.corrections c
  JOIN media_video.segments s ON s.id = c.segment_id
  JOIN media.assets a ON a.id = s.asset_id;
