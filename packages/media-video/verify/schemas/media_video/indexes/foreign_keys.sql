-- Verify schemas/media_video/indexes/foreign_keys on pg

SELECT 'media_video.decisions_chosen_shot_fk_idx'::regclass;
SELECT 'media_video.decisions_passage_id_fk_idx'::regclass;
SELECT 'media_video.decisions_rejected_passage_fk_idx'::regclass;
SELECT 'media_video.decisions_rejected_shot_fk_idx'::regclass;
SELECT 'media_video.decisions_segment_id_fk_idx'::regclass;
SELECT 'media_video.exclusions_asset_id_fk_idx'::regclass;
SELECT 'media_video.exclusions_episode_id_fk_idx'::regclass;
SELECT 'media_video.generations_input_asset_id_fk_idx'::regclass;
SELECT 'media_video.segments_cluster_id_fk_idx'::regclass;
SELECT 'media_video.selectors_model_id_fk_idx'::regclass;
SELECT 'media_video.selectors_style_id_fk_idx'::regclass;
SELECT 'media_video.speaker_clusters_persona_id_fk_idx'::regclass;
SELECT 'media_video.timeline_clips_a_asset_id_fk_idx'::regclass;
SELECT 'media_video.timeline_clips_segment_id_fk_idx'::regclass;
SELECT 'media_video.timeline_clips_shot_id_fk_idx'::regclass;
SELECT 'media_video.timeline_clips_v_asset_id_fk_idx'::regclass;
SELECT 'media_video.timelines_parent_id_fk_idx'::regclass;
SELECT 'media_video.timelines_selector_id_fk_idx'::regclass;
