-- Deploy schemas/media_audio/tables/renders to pg

-- requires: schemas/media_audio/tables/emotions
-- requires: media-core:schemas/media/tables/assets
-- requires: media-core:schemas/media/tables/voices

-- Generated audio, and enough of the settings to reproduce it. A render whose
-- parameters were not recorded cannot be improved on, only redone blind.
CREATE TABLE media_audio.renders (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  label       text NOT NULL UNIQUE,
  script_path text,
  output_path text,
  asset_id    uuid REFERENCES media.assets(id) ON DELETE SET NULL,
  engine_tts  text NOT NULL,
  engine_stt  text,
  model_size  text,
  temperature numeric,
  isolated    boolean NOT NULL DEFAULT false,
  gap_seconds numeric,
  music_path  text,
  music_vol   numeric,
  params      jsonb NOT NULL DEFAULT '{}',
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE media_audio.render_chunks (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  render_id  uuid NOT NULL REFERENCES media_audio.renders(id) ON DELETE CASCADE,
  line_no    integer NOT NULL,
  persona_id uuid REFERENCES media.personas(id) ON DELETE SET NULL,
  emotion    text REFERENCES media_audio.emotions(slug),
  text       text NOT NULL,
  audio_path text,
  seconds    numeric,
  UNIQUE (render_id, line_no)
);
