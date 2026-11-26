-- Revert schemas/media_video/indexes/redundant from pg

CREATE INDEX words_segment_idx ON media_video.words(segment_id);
CREATE INDEX vocabulary_project_idx ON media_video.vocabulary(project);
CREATE INDEX timeline_clips_timeline_idx ON media_video.timeline_clips(timeline_id, idx);
CREATE INDEX shots_asset_idx ON media_video.shots(asset_id);
CREATE INDEX segments_asset_idx ON media_video.segments(asset_id);
CREATE INDEX scores_subject_idx ON media_video.scores(subject_type, subject_id);
CREATE INDEX laughter_asset_idx ON media_video.laughter(asset_id, start_s);
CREATE INDEX frames_shot_idx ON media_video.frames(shot_id);
