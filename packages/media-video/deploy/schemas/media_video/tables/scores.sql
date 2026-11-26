-- Deploy schemas/media_video/tables/scores to pg

-- requires: schemas/media_video/tables/passages_unique

-- One ledger for every judgement about every candidate.
--
-- Scores had accumulated in the table of whatever produced them: sharpness on
-- shots, coherence on passage_quality, correlation on coordination, logprob on
-- segments. Each was correct and none could reach the others. A passage did not
-- know whether the picture behind it was any good; a shot did not know whether
-- anything was said over it. The best moment in an interview and the best-looking
-- frame were ranked by different code that never met.
--
-- So: subject, name, value, and — the parts that make it a system rather than a
-- pile —
--
--   tier      how expensive this was to compute
--   modality  which sense it came from
--   producer  what made it, so it can be invalidated when that changes
--
-- TIERS exist to enforce the budget rule. A tier may only run on what the tier
-- below it ranked. Twelve hours of 4K must never reach the expensive model, and
-- the only way to keep that true is to make cost a property of the score rather
-- than a convention in a script.
--
--   0  free        already in the row: duration, word count, turns
--   1  cheap       one pass over cheap signal: sharpness, energy, coherence
--   2  moderate    a model per item: object boxes, colour signature, pitch, coordination
--   3  expensive   an LLM or VLM per item, on the shortlist only
--
-- MODALITY exists because a cut is only good when several senses agree, and
-- because a combination that draws on one sense is a different claim from one
-- that draws on three. Recording it lets a recipe say "at least two modalities
-- must have an opinion before this ranks at all".
CREATE TABLE media_video.scores (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  subject_type text NOT NULL CHECK (subject_type IN
                 ('shot','passage','clip','asset','take')),
  subject_id   uuid NOT NULL,
  name         text NOT NULL,
  -- Normalised to 0..1 so scores are combinable; `raw` keeps the real units,
  -- because a normalised number cannot be debugged and a dB cannot be averaged
  -- with a probability.
  value        double precision NOT NULL CHECK (value >= 0 AND value <= 1),
  raw          double precision,
  tier         smallint NOT NULL DEFAULT 1 CHECK (tier BETWEEN 0 AND 3),
  modality     text NOT NULL CHECK (modality IN ('text','audio','picture','cross')),
  producer     text NOT NULL,
  computed_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (subject_type, subject_id, name, producer)
);
CREATE INDEX scores_subject_idx ON media_video.scores(subject_type, subject_id);
CREATE INDEX scores_name_idx    ON media_video.scores(name, value DESC);
CREATE INDEX scores_tier_idx    ON media_video.scores(tier, modality);

-- A named way of weighing them.
--
-- Weights are per recipe, not global, because "good" is not one function. A
-- laser reel wants visible causation between hand and beam; an interview wants a
-- question that gets a real answer. Averaging those gives something mediocre at
-- both, which is the same argument that put scope on models.
CREATE TABLE media_video.score_recipes (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name        text NOT NULL UNIQUE,
  description text,
  subject_type text NOT NULL DEFAULT 'passage',
  -- {"coherence": 0.4, "coordination": 0.2, ...}
  weights     jsonb NOT NULL,
  -- How many distinct modalities must have contributed before a result is
  -- reported at all. One sense agreeing with itself is not evidence.
  min_modalities smallint NOT NULL DEFAULT 1,
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- Combine, honestly.
--
-- The weights are renormalised over the scores that ACTUALLY EXIST for each
-- subject. A missing score is not a zero — a passage nobody has run the
-- expensive tier on is unjudged, not bad — and treating absence as failure would
-- make the cheap tier's shortlist self-fulfilling, since anything it skipped
-- would then rank last forever.
--
-- `coverage` reports how much of the recipe's weight was actually present, so a
-- confident-looking number always carries how much it was based on.
CREATE VIEW media_video.v_combined AS
SELECT r.name                              AS recipe,
       s.subject_type,
       s.subject_id,
       sum(s.value * (r.weights->>s.name)::float8)
         / nullif(sum((r.weights->>s.name)::float8), 0)  AS score,
       sum((r.weights->>s.name)::float8)                 AS coverage,
       count(DISTINCT s.modality)                        AS modalities,
       max(s.tier)                                       AS deepest_tier,
       array_agg(DISTINCT s.name ORDER BY s.name)        AS contributing
  FROM media_video.score_recipes r
  JOIN media_video.scores s
    ON s.subject_type = r.subject_type
   AND r.weights ? s.name
 GROUP BY r.name, s.subject_type, s.subject_id, r.min_modalities
HAVING count(DISTINCT s.modality) >= r.min_modalities;

INSERT INTO media_video.score_recipes (name, description, subject_type, weights, min_modalities) VALUES
  ('interview',
   'What was said, mostly. Picture matters but does not decide.',
   'passage',
   '{"coherence": 0.34, "asr_confidence": 0.16, "exchange": 0.14,
     "question_density": 0.10, "completeness": 0.10, "speech_clarity": 0.08,
     "picture_quality": 0.08}', 2),
  ('laser-reel',
   'Visible causation between a person and the lights.',
   'shot',
   '{"coordination": 0.34, "sharpness": 0.20, "energy_lift": 0.18,
     "subject_present": 0.16, "colour_variety": 0.12}', 2),
  ('broll',
   'Looks good, says nothing, safe under other audio.',
   'shot',
   '{"sharpness": 0.34, "colour_variety": 0.24, "energy_lift": 0.14,
     "safe_over_audio": 0.28}', 1)
ON CONFLICT (name) DO NOTHING;
