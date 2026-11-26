-- Deploy schemas/media/tables/geometry to pg

-- requires: schemas/media/tables/recorded_at

-- Shape, as a first-class fact about every asset.
--
-- width and height were stored and never used, so nothing downstream knew that
-- this project mixes THREE aspect ratios: the Fuji shoots 6240x4160, which is
-- 3:2 and not 16:9, the phones shoot 1920x1080, and the vertical renders are
-- 608x1080.
--
-- Rendering scaled every clip to a fixed HEIGHT and let width follow, so a 3:2
-- clip became 810x540 and a 16:9 clip became 960x540 — and concatenating those
-- produced a file that claims 810x540 throughout. Every 16:9 clip in the test
-- reel is squeezed horizontally. Nothing errored: ffmpeg took the first stream's
-- geometry and the rest inherited it.
--
-- Generated rather than computed by a caller, because a derived value that each
-- caller works out itself is a derived value that some caller gets wrong.
ALTER TABLE media.assets
  ADD COLUMN aspect numeric GENERATED ALWAYS AS (
    CASE WHEN height > 0 THEN round(width::numeric / height, 4) END) STORED,
  ADD COLUMN orientation text GENERATED ALWAYS AS (
    CASE WHEN height IS NULL OR width IS NULL THEN NULL
         WHEN width::numeric / nullif(height, 0) > 1.05 THEN 'landscape'
         WHEN width::numeric / nullif(height, 0) < 0.95 THEN 'portrait'
         ELSE 'square' END) STORED;
CREATE INDEX assets_aspect_idx ON media.assets(aspect);

-- The per-timeline geometry view lives in media-video
-- (schemas/media_video/views/timeline_geometry): media is the spine and never
-- references the domains. It was created here originally, which only worked
-- because the live database happened to have media_video already, and made
-- a fresh deploy from source fail.
