-- Deploy schemas/media/tables/assets to pg

-- requires: schemas/media

-- Every ingested file, whatever the medium.
--
-- A video-only tool would call this `sources`; an audio-only one would track
-- renders and reference audio separately. One table with a `medium` column
-- serves both, and means a question like "everything we have of this persona"
-- is one query rather than a union across systems.
--
-- content_hash is the dedupe key and it is deliberately cheap to compute
-- (size + head + tail): we are detecting "have I already ingested this file",
-- not defending against a forger, and hashing 11GB end to end costs minutes.
CREATE TABLE media.assets (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  medium        text NOT NULL CHECK (medium IN ('video','audio','image','doc')),
  path          text NOT NULL,
  content_hash  text NOT NULL UNIQUE,
  duration_s    double precision,
  width         integer,
  height        integer,
  fps           double precision,
  codec         text,
  has_audio     boolean NOT NULL DEFAULT false,
  proxy_path    text,                  -- derived, disposable
  project       text,                  -- free-form label that groups assets
  meta          jsonb NOT NULL DEFAULT '{}',
  ingested_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX assets_project_idx ON media.assets(project);
CREATE INDEX assets_medium_idx  ON media.assets(medium);
