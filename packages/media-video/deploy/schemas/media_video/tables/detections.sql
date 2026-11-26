-- Deploy schemas/media_video/tables/detections to pg

-- requires: schemas/media_video/tables/frames

-- Normalised cx cy w h, matching the vision corpus format on purpose: a
-- detection here should be promotable to a training label there with no
-- conversion step to get wrong.
CREATE TABLE media_video.detections (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  frame_id   uuid NOT NULL REFERENCES media_video.frames(id) ON DELETE CASCADE,
  class_name text NOT NULL,
  confidence double precision NOT NULL,
  cx double precision NOT NULL CHECK (cx BETWEEN 0 AND 1),
  cy double precision NOT NULL CHECK (cy BETWEEN 0 AND 1),
  w  double precision NOT NULL CHECK (w BETWEEN 0 AND 1),
  h  double precision NOT NULL CHECK (h BETWEEN 0 AND 1),
  track_id   integer,
  model      text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX detections_frame_idx ON media_video.detections(frame_id);
CREATE INDEX detections_class_idx ON media_video.detections(class_name);
