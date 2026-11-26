-- Deploy schemas/media_audio/tables/text_models to pg

-- requires: schemas/media_audio
-- requires: media-core:schemas/media/tables/personas

-- A persona can have a text model as well as a voice: one writes in their
-- register, the other speaks in their timbre. Both hang off the same person.
CREATE TABLE media_audio.text_models (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  persona_id uuid NOT NULL REFERENCES media.personas(id) ON DELETE CASCADE,
  adapter    text NOT NULL DEFAULT 'ollama' CHECK (adapter IN ('ollama','api')),
  model_ref  text NOT NULL,
  base_model text,
  notes      text,
  is_default boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (persona_id, adapter, model_ref)
);
