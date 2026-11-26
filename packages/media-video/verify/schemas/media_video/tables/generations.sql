-- Verify schemas/media_video/tables/generations on pg

SELECT 'media_video.generations'::regclass;
SELECT 'media_video.v_synthetic_assets'::regclass;
SELECT 'media_video.v_synthetic_in_timeline'::regclass;
SELECT 'media_video.v_generation_spend'::regclass;
