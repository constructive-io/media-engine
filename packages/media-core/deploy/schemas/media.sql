-- Deploy schemas/media to pg

CREATE SCHEMA media;
COMMENT ON SCHEMA media IS
  'The shared spine. Domain schemas (media_video, media_audio, media_vision) '
  'reference these tables; nothing here references them back.';
