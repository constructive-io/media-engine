-- Revert schemas/media_video/tables/audio_energy from pg

ALTER TABLE media_video.shots DROP COLUMN IF EXISTS loudness, DROP COLUMN IF EXISTS peak_db, DROP COLUMN IF EXISTS lift_db;
