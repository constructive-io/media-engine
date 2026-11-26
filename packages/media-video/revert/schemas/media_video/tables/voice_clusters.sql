-- Revert schemas/media_video/tables/voice_clusters from pg

ALTER TABLE media_video.speaker_clusters ALTER COLUMN method SET DEFAULT 'f0-bimodal';

ALTER TABLE media_video.speaker_clusters
  DROP COLUMN speech_s,
  DROP COLUMN n_windows,
  DROP COLUMN cohesion,
  DROP COLUMN centroid,
  DROP COLUMN model;
