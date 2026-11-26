-- Deploy schemas/media/tables/voices to pg

-- requires: schemas/media/tables/personas

-- A persona's voice, in both directions.
--
-- `synthesis` rows describe how to GENERATE this persona speaking — an engine, a
-- profile reference, an emotion. That is the synthesis meaning of the word.
-- `acoustic` rows describe how to RECOGNISE them — a pitch centre, later an
-- embedding. That is the recognition meaning.
--
-- Keeping them in one table with a `direction` column is the point of this
-- table: "who is speaking in this interview" and "read this line in his voice"
-- become the same lookup against the same person.
CREATE TABLE media.voices (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  persona_id           uuid NOT NULL REFERENCES media.personas(id) ON DELETE CASCADE,
  direction            text NOT NULL CHECK (direction IN ('synthesis','acoustic')),

  -- synthesis side
  engine               text,           -- name of the synthesis adapter
  emotion              text NOT NULL DEFAULT 'neutral',
  profile_ref          text,
  reference_audio_path text,
  reference_text       text,
  default_seed         integer,

  -- acoustic side
  median_f0            double precision,
  embedding            vector(256),    -- when a speaker encoder is added
  sample_asset_id      uuid REFERENCES media.assets(id) ON DELETE SET NULL,

  status               text NOT NULL DEFAULT 'ready'
                       CHECK (status IN ('ready','training','broken','provisional')),
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  CHECK (direction <> 'synthesis' OR (engine IS NOT NULL AND profile_ref IS NOT NULL))
);
CREATE INDEX voices_persona_idx ON media.voices(persona_id, direction);

-- Everything known about a persona's voices, in one row per person.
CREATE VIEW media.v_persona_voices AS
SELECT p.id, p.slug, p.name, p.kind,
       count(*) FILTER (WHERE v.direction = 'synthesis') AS synth_voices,
       count(*) FILTER (WHERE v.direction = 'acoustic')  AS acoustic_voices,
       array_remove(array_agg(DISTINCT v.engine), NULL)  AS engines,
       avg(v.median_f0) FILTER (WHERE v.direction = 'acoustic') AS median_f0
  FROM media.personas p
  LEFT JOIN media.voices v ON v.persona_id = p.id
 GROUP BY p.id, p.slug, p.name, p.kind;
