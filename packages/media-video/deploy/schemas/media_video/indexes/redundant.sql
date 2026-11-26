-- Deploy schemas/media_video/indexes/redundant to pg

-- requires: schemas/media_video/indexes/foreign_keys

-- Each of these repeats the leading columns of a unique key on the same table,
-- so it costs writes and serves no query the key does not already serve.
DROP INDEX media_video.frames_shot_idx;              -- frames (shot_id, t_s)
DROP INDEX media_video.laughter_asset_idx;           -- laughter (asset_id, start_s)
DROP INDEX media_video.scores_subject_idx;           -- scores (subject_type, subject_id, name, producer)
DROP INDEX media_video.segments_asset_idx;           -- segments (asset_id, idx)
DROP INDEX media_video.shots_asset_idx;              -- shots (asset_id, idx)
DROP INDEX media_video.timeline_clips_timeline_idx;  -- timeline_clips (timeline_id, idx)
DROP INDEX media_video.vocabulary_project_idx;       -- vocabulary (project, term)
DROP INDEX media_video.words_segment_idx;            -- words (segment_id, idx)
