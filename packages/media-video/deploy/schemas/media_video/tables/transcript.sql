-- Deploy schemas/media_video/tables/transcript to pg

-- requires: schemas/media_video
-- requires: media-core:schemas/media/tables/assets
-- requires: media-core:schemas/media/tables/personas

-- A voice found in one asset, before anyone has said whose it is.
-- Clustering is by median F0: for a two-person recording the per-segment pitch
-- distribution is bimodal, which separates speakers without a gated diarisation
-- model. Naming the cluster is a human act, done once.
CREATE TABLE media_video.speaker_clusters (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  asset_id    uuid NOT NULL REFERENCES media.assets(id) ON DELETE CASCADE,
  label       text NOT NULL,
  persona_id  uuid REFERENCES media.personas(id) ON DELETE SET NULL,
  median_f0   double precision,
  n_segments  integer,
  method      text NOT NULL DEFAULT 'f0-bimodal',
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (asset_id, label)
);

CREATE TABLE media_video.segments (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  asset_id   uuid NOT NULL REFERENCES media.assets(id) ON DELETE CASCADE,
  idx        integer NOT NULL,
  start_s    double precision NOT NULL,
  end_s      double precision NOT NULL,
  text       text NOT NULL,
  speaker    text,
  cluster_id uuid REFERENCES media_video.speaker_clusters(id) ON DELETE SET NULL,
  persona_id uuid REFERENCES media.personas(id) ON DELETE SET NULL,
  median_f0  double precision,
  importance smallint, novelty smallint, emotion smallint,
  clarity    smallint, quotability smallint,
  is_filler  boolean, is_repetition boolean,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (asset_id, idx)
);
CREATE INDEX segments_asset_idx ON media_video.segments(asset_id);
CREATE INDEX segments_persona_idx ON media_video.segments(persona_id);

-- Word timings are a table, not a jsonb blob: filler removal without them is
-- guesswork, and a cut that starts mid-phoneme is the loudest machine-editing
-- tell there is.
CREATE TABLE media_video.words (
  id         bigserial PRIMARY KEY,
  segment_id uuid NOT NULL REFERENCES media_video.segments(id) ON DELETE CASCADE,
  idx        integer NOT NULL,
  start_s    double precision,
  end_s      double precision,
  word       text NOT NULL,
  UNIQUE (segment_id, idx)
);
CREATE INDEX words_segment_idx ON media_video.words(segment_id);

CREATE VIEW media_video.v_dialogue AS
SELECT s.id, s.asset_id, s.idx, s.start_s, s.end_s, s.text,
       coalesce(p.name, p2.name, sc.label, s.speaker, 'unknown') AS who,
       s.median_f0, s.importance, s.emotion, s.quotability
  FROM media_video.segments s
  LEFT JOIN media.personas p ON p.id = s.persona_id
  LEFT JOIN media_video.speaker_clusters sc ON sc.id = s.cluster_id
  LEFT JOIN media.personas p2 ON p2.id = sc.persona_id;
