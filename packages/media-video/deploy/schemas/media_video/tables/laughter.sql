-- Deploy schemas/media_video/tables/laughter to pg

-- requires: schemas/media_video/tables/canvas

-- Where people laughed.
--
-- Asking for "funny" returned passages that TALK about being funny, because
-- humour is not a topic and an embedding retrieves topics. Laughter is a sound,
-- and the sound is not in the transcript either: whisper preserved one laughter
-- marker in 8,700 segments. It is trained to transcribe speech and discards
-- laughter as noise, so the clearest signal of a moment worth keeping is deleted
-- before anything downstream can see it.
--
-- Detected acoustically instead. Speech syllables arrive at 2-4 per second and
-- laughter at 4-8, which shows up as a peak in the modulation spectrum — the
-- spectrum of the energy envelope rather than of the audio.
--
-- Stored as spans rather than as a score on a passage, because a laugh belongs
-- to a MOMENT and the passage containing it may be long, may be on another
-- device, or may not exist. A span joins to whatever needs it.
CREATE TABLE media_video.laughter (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  asset_id      uuid NOT NULL REFERENCES media.assets(id) ON DELETE CASCADE,
  start_s       double precision NOT NULL,
  end_s         double precision NOT NULL,
  confidence    double precision NOT NULL,
  modulation_hz double precision,
  -- A human verdict, because this is a cheap detector standing in for a trained
  -- one. Confirmations here are precisely the labels a classifier would need,
  -- so reviewing candidates is how the better detector becomes possible.
  confirmed     boolean,
  detected_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (asset_id, start_s),
  CONSTRAINT laughter_span CHECK (end_s > start_s)
);
CREATE INDEX laughter_asset_idx ON media_video.laughter(asset_id, start_s);
CREATE INDEX laughter_conf_idx  ON media_video.laughter(confidence DESC);

-- Laughter mapped into take time, so it can be attached to passages on any
-- device — the laugh is usually loudest on a microphone other than the one
-- carrying the joke.
CREATE VIEW media_video.v_laughter AS
SELECT l.id, t.name AS take, ta.label AS heard_on,
       l.start_s + ta.offset_s AS take_start_s,
       l.end_s   + ta.offset_s AS take_end_s,
       l.confidence, l.modulation_hz, l.confirmed,
       substring(a.path from '[^/]+$') AS file
  FROM media_video.laughter l
  JOIN media.assets a ON a.id = l.asset_id
  JOIN media.take_assets ta ON ta.asset_id = l.asset_id
  JOIN media.takes t ON t.id = ta.take_id
 WHERE ta.offset_s IS NOT NULL;
