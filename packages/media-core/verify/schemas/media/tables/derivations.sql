-- Verify schemas/media/tables/derivations on pg

SELECT 'media.derivations'::regclass;
SELECT 'media.derivation_sources'::regclass;
SELECT 'media.v_derivations'::regclass;
