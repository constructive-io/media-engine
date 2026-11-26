-- Deploy schemas/media_video/tables/recipes to pg

-- requires: schemas/media_video/tables/coordination

-- Named ways of choosing shots, and the models that score them.
--
-- Until now "how to select" lived in CLI flags, which means it lived nowhere: a
-- compilation that worked could not be named, re-run, compared against another,
-- or pointed at a different shoot. These two tables make selection strategies
-- and scoring models first-class, for the same reason timelines are rows rather
-- than JSON files — the interesting artefact is the decision, and a decision you
-- cannot name is a decision you cannot revisit.

-- A selector is a named strategy: which shots are eligible, how many, how varied.
CREATE TABLE media_video.selectors (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name        text NOT NULL UNIQUE,
  description text,
  -- Regex over asset path. The scope is part of the strategy, not an argument
  -- to it: "laser reel" means something different from "interview b-roll" partly
  -- because of WHICH footage it draws on.
  match       text,
  params      jsonb NOT NULL DEFAULT '{}',   -- count, diversity, min_len, excerpt
  -- Which trained model scores quality for this selector, if any. Null means the
  -- hand-weighted composite.
  model_id    uuid,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

-- A model is a fitted ranker, scoped to a KIND of footage.
--
-- One taste model over everything would be wrong: what makes a laser shot good
-- (you can see the person driving the beams) has nothing to do with what makes a
-- room interview shot good (the face is sharp, the framing holds). Those are
-- different functions of different features, and averaging them produces a model
-- that is mediocre at both.
--
-- So a model carries its own scope and its own training-set definition, and
-- several can coexist. `metrics` holds the honest evaluation — held-out
-- accuracy, fold count, how many pairs it saw — so a model can never be used
-- without its provenance being one join away.
CREATE TABLE media_video.models (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name        text NOT NULL UNIQUE,
  kind        text NOT NULL DEFAULT 'shot_ranker',
  -- What this model claims to be about: 'lasers', 'interview', 'event-room'.
  scope       text,
  -- How its training set was selected, so a refit is reproducible.
  train_match text,
  train_actor text,
  features    text[] NOT NULL,
  theta       double precision[] NOT NULL,
  scale       double precision[] NOT NULL,
  n_pairs     integer,
  metrics     jsonb NOT NULL DEFAULT '{}',
  trained_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX models_scope_idx ON media_video.models(scope);

ALTER TABLE media_video.selectors
  ADD CONSTRAINT selectors_model_fk
  FOREIGN KEY (model_id) REFERENCES media_video.models(id) ON DELETE SET NULL;
