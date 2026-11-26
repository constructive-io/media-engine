-- Revert schemas/media/tables/personas from pg

DROP TABLE IF EXISTS media.persona_roles, media.personas CASCADE;
