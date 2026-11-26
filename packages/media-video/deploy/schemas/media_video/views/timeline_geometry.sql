-- Deploy schemas/media_video/views/timeline_geometry to pg

-- requires: media-core:schemas/media/tables/geometry
-- requires: schemas/media_video/tables/timelines

-- What a timeline is made of, geometrically. A cut drawing on more than one
-- shape needs a canvas decision before it is rendered, not after.
CREATE VIEW media_video.v_timeline_geometry AS
SELECT t.name AS timeline,
       count(*)                                   AS clips,
       count(DISTINCT a.aspect)                   AS distinct_aspects,
       array_agg(DISTINCT a.aspect ORDER BY a.aspect) AS aspects,
       array_agg(DISTINCT a.orientation ORDER BY a.orientation) AS orientations,
       min(a.width)  AS min_width,
       max(a.width)  AS max_width,
       -- The widest and narrowest shapes present, which together decide how much
       -- letterboxing any single canvas will cost.
       min(a.aspect) AS narrowest,
       max(a.aspect) AS widest
  FROM media_video.timelines t
  JOIN media_video.timeline_clips c ON c.timeline_id = t.id
  JOIN media.assets a ON a.id = c.v_asset_id
 WHERE a.width IS NOT NULL
 GROUP BY t.name;
