-- Deploy schemas/media_video/tables/flags to pg

-- requires: schemas/media_video/tables/scores_integrity

-- Suggested cuts, as data.
--
-- A score says a passage is good. It cannot say "good, except for the four
-- seconds of 'um, like, you know' at the start", which is the note an editor
-- actually writes. Scoring ranks candidates; flagging repairs them, and until
-- now there was no way to express the second.
--
-- Every flag is EDL-like by construction: a source, an in point, an out point,
-- and what to do about it. That makes them queryable ("show me every filler in
-- the tiktok cut"), testable ("no suggested cut may land mid-word"), and
-- applicable — a flag with `action='cut'` is a subtraction from a timeline that
-- can be previewed before it is taken.
--
-- Nothing here modifies a transcript or a media file. A flag is a SUGGESTION
-- with a confidence, sitting beside the material, exactly like every other
-- derived layer. Accepting one writes a decision; ignoring one costs nothing.
CREATE TABLE media_video.flags (
  -- Deterministic, for the same reason chunks are: a flag re-derived from the
  -- same words is the same flag, so accepting or dismissing one survives every
  -- rebuild of everything beneath it.
  id           uuid PRIMARY KEY,
  subject_type text NOT NULL DEFAULT 'segment'
               CHECK (subject_type IN ('segment','passage','clip','asset')),
  subject_id   uuid NOT NULL,
  asset_id     uuid REFERENCES media.assets(id) ON DELETE CASCADE,
  kind         text NOT NULL CHECK (kind IN
                 ('filler','false_start','repetition','long_pause','crosstalk',
                  'buried_audio','distorted','off_record','profanity',
                  'mid_sentence_start','truncated_end','hallucination')),
  -- In the ASSET's own clock, like everything else that points at media, so a
  -- flag joins the timeline without conversion.
  start_s      double precision NOT NULL,
  end_s        double precision NOT NULL,
  -- What to do. 'cut' removes the span; 'review' wants a human; 'avoid' means
  -- do not select this material in the first place.
  action       text NOT NULL DEFAULT 'review'
               CHECK (action IN ('cut','review','avoid')),
  severity     double precision NOT NULL DEFAULT 0.5
               CHECK (severity >= 0 AND severity <= 1),
  detail       text,
  producer     text NOT NULL,
  -- A human verdict on the suggestion itself, which is how the flagger learns.
  accepted     boolean,
  decided_at   timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT flags_span CHECK (end_s > start_s)
);
CREATE INDEX flags_subject_idx ON media_video.flags(subject_type, subject_id);
CREATE INDEX flags_asset_idx   ON media_video.flags(asset_id, start_s);
CREATE INDEX flags_kind_idx    ON media_video.flags(kind, severity DESC);

-- Flags against a timeline, in timeline order: the notes on a cut.
CREATE VIEW media_video.v_timeline_flags AS
SELECT t.name AS timeline, c.idx, f.kind, f.action, f.severity, f.detail,
       f.start_s AS asset_start_s, f.end_s AS asset_end_s,
       -- Where it falls inside the clip, which is what an editor needs to find
       -- it: a flag at 1840s of a source is at 3s of the cut.
       f.start_s - c.v_start_s AS clip_offset_s,
       f.accepted
  FROM media_video.flags f
  JOIN media_video.timeline_clips c
    ON c.v_asset_id = f.asset_id
   AND f.start_s < c.v_end_s AND f.end_s > c.v_start_s
  JOIN media_video.timelines t ON t.id = c.timeline_id;
