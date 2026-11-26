-- Deploy schemas/media_video/tables/canvas to pg

-- requires: schemas/media_video/tables/flags_integrity
-- requires: media-core:schemas/media/tables/geometry

-- A timeline knows its own shape.
--
-- Geometry was decided at render time by whatever the first clip happened to
-- be. That is not a decision, it is an accident with a result: a reel of 3:2 and
-- 16:9 material rendered as 810x540 throughout, with half its clips squeezed,
-- and nothing in the database recorded either the intent or the outcome.
--
-- Two columns close that. `canvas` is what this cut is FOR — a 9:16 tiktok and a
-- 16:9 episode are different edits of the same material, and which one it is
-- belongs with the timeline rather than in the flags of whoever renders it.
-- `fit` is how mismatched material is reconciled: padded and whole, or cropped
-- and filled.
ALTER TABLE media_video.timelines
  ADD COLUMN canvas text NOT NULL DEFAULT 'landscape'
    CHECK (canvas IN ('landscape','vertical','square')),
  ADD COLUMN fit text NOT NULL DEFAULT 'pad'
    CHECK (fit IN ('pad','crop'));

-- What a render actually produced, so intent and outcome can be compared.
--
-- The distortion was invisible because nothing recorded the geometry of the
-- output. A file claiming one size while containing another is exactly the kind
-- of failure that survives every check made on the database alone.
CREATE TABLE media_video.render_geometry (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  timeline     text NOT NULL,
  path         text NOT NULL,
  width        integer NOT NULL,
  height       integer NOT NULL,
  -- Sampled across the file rather than read from the header, because the header
  -- is what lied.
  uniform      boolean NOT NULL,
  samples      integer NOT NULL,
  canvas       text,
  rendered_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX render_geometry_timeline_idx ON media_video.render_geometry(timeline);

-- Every timeline whose material does not match the canvas it will be rendered
-- to, with how much of the frame will be bars. Asked BEFORE rendering.
CREATE VIEW media_video.v_canvas_fit AS
SELECT t.name AS timeline, t.canvas, t.fit,
       g.distinct_aspects, g.aspects,
       CASE t.canvas WHEN 'vertical' THEN 0.5625
                     WHEN 'square'   THEN 1.0
                     ELSE 1.7778 END AS canvas_aspect,
       -- Share of the canvas that ends up black for the WORST-fitting clip.
       -- A 3:2 source in a 16:9 canvas loses 16%; a 16:9 source in 9:16 loses
       -- 68%, which is a different edit rather than a slight adjustment.
       round((1 - LEAST(
         CASE t.canvas WHEN 'vertical' THEN 0.5625
                       WHEN 'square'   THEN 1.0
                       ELSE 1.7778 END / g.widest,
         g.narrowest / CASE t.canvas WHEN 'vertical' THEN 0.5625
                                     WHEN 'square'   THEN 1.0
                                     ELSE 1.7778 END))::numeric, 3) AS worst_letterbox
  FROM media_video.timelines t
  JOIN media_video.v_timeline_geometry g ON g.timeline = t.name;
