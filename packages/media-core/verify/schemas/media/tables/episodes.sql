-- Verify schemas/media/tables/episodes on pg

SELECT 'media.episodes'::regclass;
SELECT 'media.episode_takes'::regclass;
SELECT 'media.v_episode_sources'::regclass;
