-- Revert schemas/media/indexes/embeddings from pg

DROP INDEX IF EXISTS media.voices_embedding_idx;
