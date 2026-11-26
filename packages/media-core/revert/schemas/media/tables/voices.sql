-- Revert schemas/media/tables/voices from pg

DROP VIEW IF EXISTS media.v_persona_voices;
DROP TABLE IF EXISTS media.voices CASCADE;
