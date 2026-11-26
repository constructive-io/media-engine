-- Deploy schemas/media_video/views/shot_content to pg

-- requires: schemas/media_video/tables/variants

-- Is it safe to lay this shot over unrelated audio?
--
-- The b-roll pass put two shots over the interview in which people fill 90% of
-- the frame. A close shot of a person is a person whose mouth is readable, and
-- laying it under a different moment of the same conversation reads as broken
-- lip sync — a fault a viewer notices at once.
--
-- The distinction is measurable without any new model, because a bounding box
-- already says how big a person is in frame:
--
--   scenic       no people at all              always safe
--   wide         people under ~18% of frame    too small to read a mouth
--   group        people 18-45%                 risky; a mouth may be readable
--   talking_head anyone over 45%               never safe over other audio
--
-- The thresholds are conservative on purpose. The cost of an over-cautious
-- filter is a smaller b-roll pool; the cost of a permissive one is a cut that
-- looks broken, and a viewer who notices lip sync has stopped listening to the
-- interview.
CREATE VIEW media_video.v_shot_content AS
WITH p AS (
  SELECT f.shot_id,
         count(d.id)                            AS n_person,
         coalesce(max(d.w * d.h), 0)            AS max_person_frac,
         coalesce(avg(d.w * d.h), 0)            AS avg_person_frac
    FROM media_video.frames f
    LEFT JOIN media_video.detections d
           ON d.frame_id = f.id AND d.class_name = 'person'
          AND d.confidence >= 0.35
   GROUP BY f.shot_id
), o AS (
  SELECT f.shot_id, count(DISTINCT d.class_name) AS n_object_classes,
         string_agg(DISTINCT d.class_name, ',') AS objects
    FROM media_video.frames f
    JOIN media_video.detections d ON d.frame_id = f.id
   WHERE d.class_name <> 'person' AND d.confidence >= 0.40
   GROUP BY f.shot_id
)
SELECT sh.id AS shot_id, sh.asset_id, a.path, a.project,
       sh.idx, sh.start_s, sh.duration_s,
       coalesce(p.n_person, 0)        AS n_person,
       coalesce(p.max_person_frac, 0) AS max_person_frac,
       coalesce(o.n_object_classes,0) AS n_object_classes,
       o.objects,
       CASE
         WHEN coalesce(p.n_person, 0) = 0                 THEN 'scenic'
         WHEN coalesce(p.max_person_frac, 0) >= 0.45      THEN 'talking_head'
         WHEN coalesce(p.max_person_frac, 0) >= 0.18      THEN 'group'
         ELSE 'wide'
       END AS content_class,
       -- The single question the b-roll selector asks.
       (coalesce(p.n_person, 0) = 0 OR coalesce(p.max_person_frac, 0) < 0.18)
         AS safe_over_other_audio
  FROM media_video.shots sh
  JOIN media.assets a ON a.id = sh.asset_id
  LEFT JOIN p ON p.shot_id = sh.id
  LEFT JOIN o ON o.shot_id = sh.id
 WHERE coalesce(sh.is_black, false) = false;
