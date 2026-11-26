-- Deploy schemas/media_video/indexes/foreign_keys to pg

-- requires: schemas/media_video/tables/generations_medium

-- Every foreign key leads an index, so deleting a parent row or joining across
-- the key does not scan the child table.
CREATE INDEX decisions_chosen_shot_fk_idx ON media_video.decisions (chosen_shot);
CREATE INDEX decisions_passage_id_fk_idx ON media_video.decisions (passage_id);
CREATE INDEX decisions_rejected_passage_fk_idx ON media_video.decisions (rejected_passage);
CREATE INDEX decisions_rejected_shot_fk_idx ON media_video.decisions (rejected_shot);
CREATE INDEX decisions_segment_id_fk_idx ON media_video.decisions (segment_id);
CREATE INDEX exclusions_asset_id_fk_idx ON media_video.exclusions (asset_id);
CREATE INDEX exclusions_episode_id_fk_idx ON media_video.exclusions (episode_id);
CREATE INDEX generations_input_asset_id_fk_idx ON media_video.generations (input_asset_id);
CREATE INDEX segments_cluster_id_fk_idx ON media_video.segments (cluster_id);
CREATE INDEX selectors_model_id_fk_idx ON media_video.selectors (model_id);
CREATE INDEX selectors_style_id_fk_idx ON media_video.selectors (style_id);
CREATE INDEX speaker_clusters_persona_id_fk_idx ON media_video.speaker_clusters (persona_id);
CREATE INDEX timeline_clips_a_asset_id_fk_idx ON media_video.timeline_clips (a_asset_id);
CREATE INDEX timeline_clips_segment_id_fk_idx ON media_video.timeline_clips (segment_id);
CREATE INDEX timeline_clips_shot_id_fk_idx ON media_video.timeline_clips (shot_id);
CREATE INDEX timeline_clips_v_asset_id_fk_idx ON media_video.timeline_clips (v_asset_id);
CREATE INDEX timelines_parent_id_fk_idx ON media_video.timelines (parent_id);
CREATE INDEX timelines_selector_id_fk_idx ON media_video.timelines (selector_id);
