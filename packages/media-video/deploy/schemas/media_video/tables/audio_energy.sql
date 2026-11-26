-- Deploy schemas/media_video/tables/audio_energy to pg

-- requires: schemas/media_video/tables/shots

-- Added after the fact rather than by editing shots.sql: that change is already
-- deployed, and rewriting a deployed change is how a migration history stops
-- describing what actually happened.
ALTER TABLE media_video.shots
  ADD COLUMN IF NOT EXISTS loudness double precision,
  ADD COLUMN IF NOT EXISTS peak_db  double precision,
  ADD COLUMN IF NOT EXISTS lift_db  double precision;
