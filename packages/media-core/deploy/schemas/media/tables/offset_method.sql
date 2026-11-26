-- Deploy schemas/media/tables/offset_method to pg

-- requires: schemas/media/tables/takes

-- How an offset was arrived at.
--
-- An offset alone says where a recording sits in its take. It does not say
-- why anyone should believe it, and the confidence beside it is a number with
-- no unit until the method is known: shared words for one, a peak's height
-- for another.
--
--   words     the words both recordings share voted for it; good to half a second
--   phrases   as words, then measured from phrases each said once; the precise one
--   loudness  the two sound alike here; for recordings with nothing said
--   clock     the devices' own clocks, once those were shown to agree
--   manual    a person set it; sync leaves it alone and builds on it
--
-- Null means nothing has placed this recording yet. The primary recording is
-- zero by definition and needs no method.
ALTER TABLE media.take_assets ADD COLUMN offset_method text
  CHECK (offset_method IN ('words','phrases','loudness','clock','manual'));
