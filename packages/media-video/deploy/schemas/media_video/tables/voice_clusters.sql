-- Deploy schemas/media_video/tables/voice_clusters to pg

-- requires: schemas/media_video/tables/transcript

-- A voice is found by what it sounds like, not by its pitch: a model turns
-- each second or two of speech into numbers that sit close together for one
-- person and apart for two, and the numbers are grouped.
--
-- The centre of a group is kept beside it, with the model that made it. That
-- is what lets a recording be gone over again without losing the names its
-- voices were given, and what lets a voice named in one recording be
-- recognised in the next. Centres from different models cannot be compared,
-- which is why the model is recorded.
ALTER TABLE media_video.speaker_clusters
  ADD COLUMN model     text,
  ADD COLUMN centroid  real[],
  ADD COLUMN cohesion  double precision,
  ADD COLUMN n_windows integer,
  ADD COLUMN speech_s  double precision;

-- Nothing is assumed about how a voice was found; whatever found it says so.
ALTER TABLE media_video.speaker_clusters ALTER COLUMN method DROP DEFAULT;

COMMENT ON COLUMN media_video.speaker_clusters.model IS
  'the model whose numbers the centre is in';
COMMENT ON COLUMN media_video.speaker_clusters.centroid IS
  'the middle of the voice, at unit length, in the numbers of `model`';
COMMENT ON COLUMN media_video.speaker_clusters.cohesion IS
  'how alike the pieces of this voice are, 0 to 1; a low figure suggests more than one person';
COMMENT ON COLUMN media_video.speaker_clusters.n_windows IS
  'how many pieces of speech the voice was found from';
COMMENT ON COLUMN media_video.speaker_clusters.speech_s IS
  'seconds of speech given to this voice';
