-- Revert schemas/media/tables/assets_path_key from pg

DROP INDEX IF EXISTS media.assets_path_key;
