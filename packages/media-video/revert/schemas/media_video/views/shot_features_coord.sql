-- Revert schemas/media_video/views/shot_features_coord from pg

DROP VIEW IF EXISTS media_video.v_preference_pairs;
DROP VIEW IF EXISTS media_video.v_shot_features;
