-- Revert schemas/media_video/tables/canvas from pg

DROP VIEW  IF EXISTS media_video.v_canvas_fit;
DROP TABLE IF EXISTS media_video.render_geometry CASCADE;
ALTER TABLE media_video.timelines DROP COLUMN IF EXISTS fit;
ALTER TABLE media_video.timelines DROP COLUMN IF EXISTS canvas;
