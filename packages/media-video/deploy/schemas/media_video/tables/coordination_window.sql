-- Deploy schemas/media_video/tables/coordination_window to pg

-- requires: schemas/media_video/tables/coordination

-- Where in the shot the coordination actually happens.
--
-- Scoring a whole take with one correlation made the number fall with length —
-- measured at corr(coordination, duration) = -0.339 — because a long take
-- averages over drawing, pausing, walking and talking. An editor uses the good
-- bit, so the score is now the best 4-second window and this column says where
-- it starts. That makes the ranking signal and the excerpt point the same
-- computation instead of two.
ALTER TABLE media_video.coordination ADD COLUMN best_at_s double precision;
