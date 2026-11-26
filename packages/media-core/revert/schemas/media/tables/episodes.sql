-- Revert schemas/media/tables/episodes from pg

DROP VIEW  IF EXISTS media.v_episode_sources;
DROP TABLE IF EXISTS media.episode_assets CASCADE;
DROP TABLE IF EXISTS media.episode_takes CASCADE;
DROP TABLE IF EXISTS media.episodes CASCADE;
