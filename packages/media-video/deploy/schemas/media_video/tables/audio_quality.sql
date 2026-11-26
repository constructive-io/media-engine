-- Deploy schemas/media_video/tables/audio_quality to pg

-- requires: schemas/media_video/tables/asr_model

-- How good does this actually SOUND?
--
-- The pipeline knew whether the recogniser was confident, which is a statement
-- about a model, not about audio. A lav under a coat produces confident
-- nonsense; a clear recording of someone across the room produces uncertain
-- truth. Nothing measured the signal itself.
--
-- Per segment, because a recording is not uniformly good and the useful question
-- is always "is THIS bit usable":
--
--   rms_db          how loud the speech is
--   noise_db        the floor around it, from the quiet parts of the same file
--   snr_db          the difference, which is what "clear" means
--   clipped_ratio   samples at full scale; distortion no gain can undo
--   hf_ratio        energy above 4kHz over total. Speech through cloth or a
--                   phone loses its highs, so this separates "muffled" from
--                   "quiet" — two problems that look identical on a level meter
--                   and need opposite fixes.
--
-- Noise floor comes from the WHOLE file rather than the segment: a floor
-- measured inside speech is not a floor. This is the same lesson as the mixer's
-- reference level, which was computed over too short a window and produced a
-- 667x error.
CREATE TABLE media_video.audio_quality (
  segment_id    uuid PRIMARY KEY REFERENCES media_video.segments(id) ON DELETE CASCADE,
  rms_db        double precision,
  noise_db      double precision,
  snr_db        double precision,
  clipped_ratio double precision,
  hf_ratio      double precision,
  computed_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audio_quality_snr_idx ON media_video.audio_quality(snr_db);

CREATE VIEW media_video.v_audible AS
SELECT s.id AS segment_id, s.asset_id, s.start_s, s.end_s, s.text,
       s.avg_logprob, q.snr_db, q.rms_db, q.noise_db, q.hf_ratio,
       q.clipped_ratio,
       -- A blunt verdict, so a query can ask for usable audio without
       -- remembering thresholds. Broadcast dialogue wants better than 20dB;
       -- under 6dB the words are competing with the room.
       CASE WHEN q.snr_db IS NULL           THEN 'unmeasured'
            WHEN q.clipped_ratio > 0.005    THEN 'distorted'
            WHEN q.snr_db >= 20             THEN 'clean'
            WHEN q.snr_db >= 12             THEN 'usable'
            WHEN q.snr_db >= 6              THEN 'noisy'
            ELSE 'buried' END               AS verdict
  FROM media_video.segments s
  LEFT JOIN media_video.audio_quality q ON q.segment_id = s.id;
