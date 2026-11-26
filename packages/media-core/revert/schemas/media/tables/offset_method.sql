-- Revert schemas/media/tables/offset_method from pg

ALTER TABLE media.take_assets DROP COLUMN IF EXISTS offset_method;
