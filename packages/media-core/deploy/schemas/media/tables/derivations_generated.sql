-- Deploy schemas/media/tables/derivations_generated to pg

-- requires: schemas/media/tables/derivations

-- A file a model made from a description is a derived file of its own kind.
--
-- It is not a render, which is cut from recordings, and not a still, which is
-- one frame of one. It has no recording behind it at all, or one picture it
-- started from. Kept apart, "which of these files did nobody film" is a
-- question the lineage answers by itself, and `media outputs` can list them.
ALTER TABLE media.derivations DROP CONSTRAINT derivations_kind_check;
ALTER TABLE media.derivations ADD CONSTRAINT derivations_kind_check
  CHECK (kind IN ('proxy','render','snippet','stem','still','export','generated'));
