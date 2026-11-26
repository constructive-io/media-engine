-- Revert schemas/media_video/tables/transcript from pg

DROP VIEW IF EXISTS media_video.v_dialogue;
DROP TABLE IF EXISTS media_video.words;
DROP TABLE IF EXISTS media_video.segments;
DROP TABLE IF EXISTS media_video.speaker_clusters;
