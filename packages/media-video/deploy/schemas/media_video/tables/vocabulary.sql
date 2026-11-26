-- Deploy schemas/media_video/tables/vocabulary to pg

-- requires: schemas/media_video/tables/passage_quality

-- The words this project contains that a general model will not guess.
--
-- A general recogniser hears "Lama" for Llama and "Jigawatt" for gigawatt, and
-- mangles company and personal names. Those are proper
-- nouns and domain terms — exactly what makes a passage worth keeping, and
-- exactly what a general model gets wrong.
--
-- It matters more than it looks, because coherence RANKS on the transcript. A
-- garbled proper noun lowers lexical variety and raises apparent repetition, so
-- bad recognition does not merely produce an ugly caption: it silently demotes
-- the most interesting content in the project.
--
-- Measured on a single file, base + a vocabulary prompt beat `small` + the same
-- prompt (llama 5/5 against 0/5) at half the runtime. Telling the model which
-- words exist is worth more than giving it more parameters — so the vocabulary
-- is a first-class thing that grows with the project rather than a constant in
-- the code.
CREATE TABLE media_video.vocabulary (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project    text,
  term       text NOT NULL,
  -- What it tends to be misheard as, so a later pass can find and repair
  -- transcripts that were made before the term was known.
  confusions text[] NOT NULL DEFAULT '{}',
  kind       text,                       -- 'person', 'company', 'technical'
  added_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (project, term)
);
CREATE INDEX vocabulary_project_idx ON media_video.vocabulary(project);

-- How confident the recogniser was. Without this, nothing downstream can tell
-- "this person said something unusual" from "the model failed here", and the
-- ranking treats both as equally real.
ALTER TABLE media_video.segments
  ADD COLUMN avg_logprob       double precision,
  ADD COLUMN no_speech_prob    double precision,
  ADD COLUMN compression_ratio double precision;
