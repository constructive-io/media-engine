-- Verify schemas/media/tables/takes on pg

SELECT 'media.takes'::regclass; SELECT 'media.take_assets'::regclass; SELECT 'media.v_take_assets'::regclass;
