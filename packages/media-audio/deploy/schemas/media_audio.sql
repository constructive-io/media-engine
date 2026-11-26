-- Deploy schemas/media_audio to pg

CREATE SCHEMA media_audio;
COMMENT ON SCHEMA media_audio IS
  'Speech synthesis and its review. Voices themselves live in media.voices with '
  'direction=synthesis, alongside the acoustic voices used to recognise the same '
  'people in footage.';
