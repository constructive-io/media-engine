-- Deploy schemas/media/tables/assets_path_key to pg

-- requires: schemas/media/tables/assets

-- One row per file on disk.
--
-- assets was keyed on content_hash alone, which is right for ingested footage —
-- the same card copied to two folders is one asset. It is wrong for derived
-- files. An ffmpeg encode is not bit-reproducible, so re-rendering a timeline to
-- the same path produced a new hash, a second row, and a second provenance edge
-- alongside the first. The timeline then read as drawing on twice the sources it
-- actually did.
--
-- The code now looks up derived outputs by path, but code is the wrong place for
-- this to live alone: any future producer that forgets would reintroduce the
-- duplicate silently, and a wrong provenance row is worse than a missing one
-- because it still answers the question.
CREATE UNIQUE INDEX assets_path_key ON media.assets(path);
