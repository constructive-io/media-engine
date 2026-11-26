-- Revert schemas/media_video/tables/passages_unique from pg

DROP INDEX IF EXISTS media_video.passages_span_key;
