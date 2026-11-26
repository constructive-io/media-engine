-- Deploy schemas/media_video/tables/shots to pg

-- requires: schemas/media_video
-- requires: media-core:schemas/media/tables/assets

-- Times in seconds from the start of the asset, never frame numbers: a frame
-- number means nothing without a frame rate, and variable-frame-rate footage
-- exists.
CREATE TABLE media_video.shots (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  asset_id    uuid NOT NULL REFERENCES media.assets(id) ON DELETE CASCADE,
  idx         integer NOT NULL,
  start_s     double precision NOT NULL,
  end_s       double precision NOT NULL,
  duration_s  double precision GENERATED ALWAYS AS (end_s - start_s) STORED,
  sharpness   double precision,
  exposure    double precision,
  motion      double precision,
  is_black    boolean,
  shot_size   text CHECK (shot_size IN ('wide','medium','close','none')),

  -- Audio, because on a live event the crowd is the best interest signal there
  -- is. loudness is the shot's own mean dBFS; peak is its loudest moment; lift
  -- is how far above the surrounding minute it sits — which is what actually
  -- marks a reaction, since a uniformly loud show has no informative absolute
  -- level.
  loudness    double precision,
  peak_db     double precision,
  lift_db     double precision,
  subject_n   integer,
  subject_area double precision,
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (asset_id, idx)
);
CREATE INDEX shots_asset_idx ON media_video.shots(asset_id);
