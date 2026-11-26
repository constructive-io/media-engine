-- Revert schemas/media_video/indexes/embeddings from pg

DROP INDEX IF EXISTS media_video.frames_embedding_idx;
