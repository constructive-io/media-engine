-- Verify schemas/media_video/tables/generations_medium on pg

SELECT medium FROM media_video.generations WHERE false;
SELECT 'media_video.v_generation_chain'::regclass;
