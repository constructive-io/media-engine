-- Deploy schemas/media_video/tables/passage_quality to pg

-- requires: schemas/media_video/tables/styles

-- Whether a passage is worth listening to, as opposed to well shaped.
--
-- The ranking so far counted words, turns, questions and dead air. All
-- structural, and all satisfiable by a passage in which nothing coherent is
-- said. One viewing of a cut shows both failure modes: an opening scene where
-- not much is being said coherently, and a closing clip that ends mid-sentence,
-- cut off exactly where it became interesting.
--
-- Neither is visible to a word count. These columns make them visible.
--
--   ends_complete    the last sentence finished; the clip does not stop mid-thought
--   starts_complete  the first sentence began; it does not open mid-sentence
--   repetition       whisper repeats itself when the audio is poor, and repeated
--                    text is the cheapest available proxy for "this was hard to
--                    hear", which is usually also hard to listen to
--   fragment_ratio   proportion of very short utterances — a passage of "Yeah."
--                    "Right." "Sure." has a fine word count and says nothing
--   lexical_variety  unique words over total; low means circling one idea
--   coherence        the combination, so ranking can use one number
CREATE TABLE media_video.passage_quality (
  passage_id      uuid PRIMARY KEY REFERENCES media_video.passages(id) ON DELETE CASCADE,
  ends_complete   boolean,
  starts_complete boolean,
  -- Seconds of speech immediately after the passage ends. Large means the
  -- speaker stopped; near zero means we cut them off, which is the specific
  -- fault a listener hears.
  tail_gap_s      double precision,
  head_gap_s      double precision,
  repetition      double precision,
  fragment_ratio  double precision,
  lexical_variety double precision,
  mean_sentence_w double precision,
  coherence       double precision,
  computed_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX passage_quality_coherence_idx
  ON media_video.passage_quality(coherence DESC NULLS LAST);

CREATE VIEW media_video.v_passages AS
SELECT p.*, t.name AS take,
       q.ends_complete, q.starts_complete, q.tail_gap_s, q.head_gap_s,
       q.repetition, q.fragment_ratio, q.lexical_variety, q.coherence
  FROM media_video.passages p
  JOIN media.takes t ON t.id = p.take_id
  LEFT JOIN media_video.passage_quality q ON q.passage_id = p.id;
