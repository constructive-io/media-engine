-- Deploy schemas/media_video/tables/coordination to pg

-- requires: schemas/media_video/tables/shots

-- Does the picture show cause and effect?
--
-- An interesting shot of someone working a loom is one where you can see
-- the threads move while the person moves the shuttle: the coordination
-- between the two. That is not motion, which the pipeline
-- already measured and which ranks a whip pan over a person drawing. It is TWO
-- things moving together.
--
-- `corr` is the correlation between beam-pixel motion and everything-else motion
-- across the shot. `lag_s` is where that correlation peaked: positive means the
-- scene moved first and the beams followed, which is the direction that means a
-- person is driving the lights rather than standing in front of them.
CREATE TABLE media_video.coordination (
  shot_id       uuid PRIMARY KEY REFERENCES media_video.shots(id) ON DELETE CASCADE,
  corr          double precision,     -- null when there are no beams to correlate
  lag_s         double precision,
  beam_motion   double precision,
  scene_motion  double precision,
  beam_pixels   double precision,
  n_frames      integer,
  computed_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX coordination_corr_idx ON media_video.coordination(corr DESC NULLS LAST);
