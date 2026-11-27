-- Revert schemas/media/tables/derivations_generated from pg

-- Not validated: what was recorded as generated stays on record, and nothing
-- new can be.
ALTER TABLE media.derivations DROP CONSTRAINT IF EXISTS derivations_kind_check;
ALTER TABLE media.derivations ADD CONSTRAINT derivations_kind_check
  CHECK (kind IN ('proxy','render','snippet','stem','still','export')) NOT VALID;
