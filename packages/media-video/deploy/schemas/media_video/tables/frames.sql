-- Deploy schemas/media_video/tables/frames to pg

-- requires: schemas/media_video/tables/shots

CREATE TABLE media_video.frames (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  shot_id    uuid NOT NULL REFERENCES media_video.shots(id) ON DELETE CASCADE,
  t_s        double precision NOT NULL,
  path       text,
  sharpness  double precision,
  exposure   double precision,
  brightness double precision,
  embedding  vector(1024),          -- tier 3, when an encoder is added
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (shot_id, t_s)
);
CREATE INDEX frames_shot_idx ON media_video.frames(shot_id);
