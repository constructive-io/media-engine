-- Deploy schemas/media_video/indexes/embeddings to pg

-- requires: schemas/media_video/indexes/redundant

-- Frame embeddings are searched by cosine distance. Without an index every
-- "find frames like this one" reads the whole table.
CREATE INDEX frames_embedding_idx ON media_video.frames USING hnsw (embedding vector_cosine_ops);
