-- Verify schemas/media/tables/derivations_generated on pg

DO $$
BEGIN
  ASSERT (SELECT pg_get_constraintdef(oid) LIKE '%generated%' FROM pg_constraint
           WHERE conrelid = 'media.derivations'::regclass AND conname = 'derivations_kind_check'),
         'media.derivations does not allow the kind generated';
END $$;
