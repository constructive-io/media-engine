-- Deploy schemas/media_video/views/shot_features_coord to pg

-- requires: schemas/media_video/views/shot_features
-- requires: schemas/media_video/tables/coordination

-- Rebuild v_shot_features with coordination included.
--
-- pgpm never edits a deployed change, so adding a column to a view is a NEW
-- change that drops and recreates it. That is the deterministic-migration
-- discipline: the previous change stays exactly as it was deployed, and the
-- history says when the column appeared and why.
DROP VIEW IF EXISTS media_video.v_preference_pairs;
DROP VIEW IF EXISTS media_video.v_shot_features;

CREATE VIEW media_video.v_shot_features AS
SELECT sh.id AS shot_id, sh.asset_id, a.path, a.project,
       coalesce(a.project, split_part(a.path, '/', -2))  AS shoot,
       coalesce(sh.duration_s, 0)  AS duration_s,
       coalesce(sh.sharpness, 0)   AS sharpness,
       coalesce(sh.exposure, 0)    AS exposure,
       coalesce(sh.motion, g.motion, 0) AS motion,
       coalesce(sh.lift_db, 0)     AS lift_db,
       coalesce(sh.loudness, 0)    AS loudness,
       coalesce(sh.subject_n, 0)   AS subject_n,
       coalesce(sh.subject_area, 0) AS subject_area,
       CASE sh.shot_size WHEN 'close' THEN 1.0 WHEN 'medium' THEN 0.5
                         WHEN 'wide' THEN 0.0 ELSE 0.5 END AS shot_size_n,
       coalesce(g.sat_mean, 0)     AS sat_mean,
       coalesce(g.dominant_h, 0)   AS dominant_h,
       coalesce((SELECT sum(h) FROM unnest(g.hue[1:4]) h), 0)  AS hue_warm,
       coalesce((SELECT sum(h) FROM unnest(g.hue[9:16]) h), 0) AS hue_cool,
       coalesce((SELECT sum(h) FROM unnest(g.hue[5:8]) h), 0)  AS hue_green,
       -- The coordination feature: can you see the person causing the lasers to
       -- move. Null means "no beams to correlate", which is not the same as no
       -- coordination, so it is exposed as a value AND a flag rather than
       -- collapsed into zero.
       coalesce(co.corr, 0)                AS coordination,
       (co.corr IS NOT NULL)::int::float8  AS has_beams,
       coalesce(co.lag_s, 0)               AS coord_lag_s,
       coalesce((SELECT count(*) FROM media_video.detections d
                  JOIN media_video.frames f ON f.id = d.frame_id
                 WHERE f.shot_id = sh.id AND d.class_name = 'person'), 0)::float8
                                           AS person_boxes
  FROM media_video.shots sh
  JOIN media.assets a ON a.id = sh.asset_id
  LEFT JOIN media_video.signatures g ON g.shot_id = sh.id
  LEFT JOIN media_video.coordination co ON co.shot_id = sh.id
 WHERE coalesce(sh.is_black, false) = false;

CREATE VIEW media_video.v_preference_pairs AS
SELECT d.id AS decision_id, d.actor, d.source_of, d.context, d.decided_at,
       w.shot_id AS won_shot, l.shot_id AS lost_shot,
       w.shoot AS won_shoot, l.shoot AS lost_shoot, w, l
  FROM media_video.decisions d
  JOIN media_video.v_shot_features w ON w.shot_id = d.chosen_shot
  JOIN media_video.v_shot_features l ON l.shot_id = d.rejected_shot
 WHERE d.kind = 'shot_pref';
