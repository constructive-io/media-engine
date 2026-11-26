-- Deploy schemas/media_video to pg

CREATE SCHEMA media_video;
COMMENT ON SCHEMA media_video IS
  'Video analysis and editing. References media.assets and media.personas; '
  'the spine never references back.';
