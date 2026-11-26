-- Deploy schemas/media/indexes/embeddings to pg

-- requires: schemas/media/tables/voices

-- Speaker embeddings are compared by cosine distance. Without an index every
-- "whose voice is this" reads the whole table.
CREATE INDEX voices_embedding_idx ON media.voices USING hnsw (embedding vector_cosine_ops);
