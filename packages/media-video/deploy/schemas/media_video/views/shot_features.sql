-- Deploy schemas/media_video/views/shot_features to pg

-- requires: schemas/media_video/tables/decisions

-- The training set, assembled in the database rather than in Python.
--
-- Everything a ranker needs about a shot, in one row, so fitting a model is a
-- SELECT and not a data-loading script. That matters more than it sounds: the
-- moment feature assembly lives in Python, the features used at training time
-- and the features used at scoring time are two pieces of code that drift, and
-- nothing tells you when they have.
--
-- Every column is coalesced. A single NULL in a feature vector poisons a whole
-- row, and this pipeline has already lost a ranking that way once —
-- shots.motion was unpopulated, so a NULL max propagated through a composite and
-- every score printed 0.000.
CREATE VIEW media_video.v_shot_features AS
SELECT sh.id                                   AS shot_id,
       sh.asset_id,
       a.path,
       a.project,
       -- The group for held-out evaluation. Hold out SHOOTS, not shots:
       -- neighbouring shots share lighting, lens and location, so a random
       -- split lets the model memorise a session and report an accuracy it
       -- cannot reproduce on new footage.
       coalesce(a.project, split_part(a.path, '/', -2))  AS shoot,
       coalesce(sh.duration_s, 0)              AS duration_s,
       coalesce(sh.sharpness, 0)               AS sharpness,
       coalesce(sh.exposure, 0)                AS exposure,
       coalesce(sh.motion, g.motion, 0)        AS motion,
       coalesce(sh.lift_db, 0)                 AS lift_db,
       coalesce(sh.loudness, 0)                AS loudness,
       coalesce(sh.subject_n, 0)               AS subject_n,
       -- Subject size as a fraction of frame, and the shot size it implies.
       -- This is the variable a person actually weighs when choosing between two
       -- takes — do not cut two wides together; a close-up lands a beat — and it
       -- falls out of a bounding box with no model of its own.
       coalesce(sh.subject_area, 0)            AS subject_area,
       CASE sh.shot_size WHEN 'close' THEN 1.0 WHEN 'medium' THEN 0.5
                         WHEN 'wide' THEN 0.0 ELSE 0.5 END AS shot_size_n,
       coalesce(g.sat_mean, 0)                 AS sat_mean,
       coalesce(g.dominant_h, 0)               AS dominant_h,
       -- Colour as three coarse bands rather than 24 bins. With a few hundred
       -- preference rows, 24 correlated features would fit noise; these are the
       -- distinctions a person actually names — warm, cool, green.
       coalesce((SELECT sum(h) FROM unnest(g.hue[1:4]) h), 0)    AS hue_warm,
       coalesce((SELECT sum(h) FROM unnest(g.hue[9:16]) h), 0)   AS hue_cool,
       coalesce((SELECT sum(h) FROM unnest(g.hue[5:8]) h), 0)    AS hue_green,
       -- The working definition of interesting: can you see the person causing
       -- the lasers to move. Null when a shot has no beams to correlate, which
       -- is different from zero coordination and must not be coalesced into it
       -- for the ranker — so it is exposed twice, as a value and as a flag.
       coalesce(co.corr, 0)                    AS coordination,
       (co.corr IS NOT NULL)::int::float8      AS has_beams,
       coalesce(co.lag_s, 0)                   AS coord_lag_s,
       coalesce((SELECT count(*) FROM media_video.detections d
                  JOIN media_video.frames f ON f.id = d.frame_id
                 WHERE f.shot_id = sh.id AND d.class_name = 'person'), 0)::float8
                                               AS person_boxes
  FROM media_video.shots sh
  JOIN media.assets a ON a.id = sh.asset_id
  LEFT JOIN media_video.signatures g ON g.shot_id = sh.id
  LEFT JOIN media_video.coordination co ON co.shot_id = sh.id
 WHERE coalesce(sh.is_black, false) = false;

-- Preferences with both sides' features already attached: one row per training
-- example. A ranker learns from the DIFFERENCE between the two vectors, so
-- emitting them side by side keeps that subtraction in one place.
CREATE VIEW media_video.v_preference_pairs AS
SELECT d.id            AS decision_id,
       d.actor,
       d.source_of,
       d.context,
       d.decided_at,
       w.shot_id       AS won_shot,
       l.shot_id       AS lost_shot,
       w.shoot         AS won_shoot,
       l.shoot         AS lost_shoot,
       w, l
  FROM media_video.decisions d
  JOIN media_video.v_shot_features w ON w.shot_id = d.chosen_shot
  JOIN media_video.v_shot_features l ON l.shot_id = d.rejected_shot
 WHERE d.kind = 'shot_pref';
