-- Deploy schemas/media_video/tables/passages_unique to pg

-- requires: schemas/media_video/tables/vocabulary

-- One passage per span, per proposer, per take.
--
-- `propose` had no notion of having run before, and the overnight script runs it
-- every night. 4,255 rows held 1,847 distinct spans — 57% duplicates,
-- accumulating on every run. They surfaced in the review script as the same
-- paragraph twice in a row, which is where a human notices, but they had
-- already been corrupting the project ranking and every quality average.
--
-- Rounded to a hundredth of a second because boundaries are derived from
-- floating point arithmetic over segment times, and two runs of the same
-- proposer can differ in the last bits without differing in any way that
-- matters.
CREATE UNIQUE INDEX passages_span_key ON media_video.passages
  (take_id, round(start_s::numeric, 2), round(end_s::numeric, 2), method);
