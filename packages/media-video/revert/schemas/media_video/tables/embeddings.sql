-- Revert schemas/media_video/tables/embeddings from pg

DROP VIEW  IF EXISTS media_video.v_searchable;
DROP TABLE IF EXISTS media_video.passage_embeddings CASCADE;
