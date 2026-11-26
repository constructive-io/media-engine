-- Revert schemas/media/tables/takes from pg

DROP VIEW IF EXISTS media.v_take_assets;
DROP TABLE IF EXISTS media.take_assets, media.takes CASCADE;
