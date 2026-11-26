-- Verify schemas/media_video/tables/embeddings on pg

SELECT 'media_video.passage_embeddings'::regclass;
SELECT 'media_video.v_searchable'::regclass;
