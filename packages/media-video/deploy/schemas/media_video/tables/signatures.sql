-- Deploy schemas/media_video/tables/signatures to pg

-- requires: schemas/media_video/tables/shots

-- Colour + motion, for answering "is this like the last one".
--
-- Deliberately not a CLIP embedding. CLIP is trained on natural photographs and
-- abstract laser patterns in a dark room are out of distribution — two very
-- different scenes land close together simply because both are "dark image with
-- coloured light". A hue histogram has no such problem.
CREATE TABLE media_video.signatures (
  shot_id     uuid PRIMARY KEY REFERENCES media_video.shots(id) ON DELETE CASCADE,
  hue         double precision[] NOT NULL,
  value       double precision[] NOT NULL,
  sat_mean    double precision,
  motion      double precision,
  dominant_h  double precision,
  computed_at timestamptz NOT NULL DEFAULT now()
);
