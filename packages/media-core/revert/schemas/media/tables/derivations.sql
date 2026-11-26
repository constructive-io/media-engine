-- Revert schemas/media/tables/derivations from pg

DROP VIEW  IF EXISTS media.v_derivations;
DROP TABLE IF EXISTS media.derivation_sources CASCADE;
DROP TABLE IF EXISTS media.derivations CASCADE;
