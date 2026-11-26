-- Revert schemas/media_audio/tables/renders from pg

DROP TABLE IF EXISTS media_audio.render_chunks;
DROP TABLE IF EXISTS media_audio.renders;
