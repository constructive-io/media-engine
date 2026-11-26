-- Deploy schemas/media_video/tables/passages to pg

-- requires: schemas/media_video/tables/recipes
-- requires: schemas/media_video/tables/transcript

-- A stretch of conversation, which is the unit an interview is judged in.
--
-- Everything the decision log could express until now was shot-centric:
-- shot_pref, shot_keep, cut_point. That is the right unit for a compilation of
-- laser footage and the wrong one for an interview. Nobody says "that shot was a
-- good conversation" — they say "that bit where he talks about why he moved to
-- Oakland was great", and that bit spans several shots, several speakers, and
-- possibly several microphones.
--
-- So a passage is a span of TAKE time, not of one asset: it is device-independent
-- by construction, which is what makes it the unit that can be judged once and
-- then cut from whichever camera was best at that moment.
CREATE TABLE media_video.passages (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  take_id       uuid NOT NULL REFERENCES media.takes(id) ON DELETE CASCADE,
  -- In take time, so it survives a re-sync and applies to every device at once.
  start_s       double precision NOT NULL,
  end_s         double precision NOT NULL,
  -- How this candidate was proposed: 'gap' (silence-bounded), 'topic', 'llm',
  -- 'human'. Kept because a ranker trained only on candidates one proposer
  -- generated learns that proposer's blind spots as if they were taste.
  method        text NOT NULL DEFAULT 'gap',
  text          text,
  n_words       integer,
  n_turns       integer,          -- speaker changes: conversation vs monologue
  n_speakers    integer,
  question_n    integer,          -- interviewer engagement
  filler_n      integer,
  longest_gap_s double precision, -- dead air, which kills a passage
  overlap_s     double precision, -- people talking over each other
  -- Room for scores that need a model to produce: quotability, emotion,
  -- clarity, topic. Deliberately jsonb — these arrive over time from different
  -- scorers and forcing them into columns means a migration per idea.
  scores        jsonb NOT NULL DEFAULT '{}',
  created_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT passages_span CHECK (end_s > start_s)
);
CREATE INDEX passages_take_idx ON media_video.passages(take_id, start_s);

-- The decision log has to be able to point at one.
ALTER TABLE media_video.decisions
  ADD COLUMN passage_id  uuid REFERENCES media_video.passages(id) ON DELETE CASCADE,
  ADD COLUMN rejected_passage uuid REFERENCES media_video.passages(id) ON DELETE CASCADE;

ALTER TABLE media_video.decisions DROP CONSTRAINT decisions_kind_check;
ALTER TABLE media_video.decisions ADD CONSTRAINT decisions_kind_check
  CHECK (kind IN ('shot_pref','shot_keep','segment_keep','cut_point',
                  'passage_pref','passage_keep'));
