-- Deploy schemas/media_video/tables/timelines to pg

-- requires: schemas/media_video/tables/transcript
-- requires: schemas/media_video/tables/shots
-- requires: media-core:schemas/media/tables/assets

-- The edit, as data. An EDL is not a byproduct of editing — it IS the edit, and
-- as a file it cannot be queried, diffed against yesterday's cut, or joined back
-- to the shots and passages it came from.
CREATE TABLE media_video.timelines (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name       text NOT NULL UNIQUE,
  kind       text NOT NULL DEFAULT 'cut'
             CHECK (kind IN ('cut','compilation','script','mixed')),
  project    text,
  title      text,
  logline    text,
  params     jsonb,
  created_by text NOT NULL DEFAULT 'constructive-media',
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Picture and sound are separate columns because the defining documentary move
-- is holding an interview answer on the audio track while the picture cuts away.
-- Assets are referenced by id, not path: a proxy can be re-encoded, and a
-- timeline that breaks when that happens records nothing.
CREATE TABLE media_video.timeline_clips (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  timeline_id uuid NOT NULL REFERENCES media_video.timelines(id) ON DELETE CASCADE,
  idx         integer NOT NULL,
  v_asset_id  uuid NOT NULL REFERENCES media.assets(id) ON DELETE CASCADE,
  v_start_s   double precision NOT NULL,
  v_end_s     double precision NOT NULL,
  a_asset_id  uuid REFERENCES media.assets(id) ON DELETE SET NULL,
  a_start_s   double precision,
  a_end_s     double precision,
  shot_id     uuid REFERENCES media_video.shots(id) ON DELETE SET NULL,
  segment_id  uuid REFERENCES media_video.segments(id) ON DELETE SET NULL,
  label       text,
  note        text,
  CHECK (v_end_s > v_start_s),
  UNIQUE (timeline_id, idx)
);
CREATE INDEX timeline_clips_timeline_idx ON media_video.timeline_clips(timeline_id, idx);

CREATE VIEW media_video.v_timeline AS
SELECT t.name, t.kind, t.title, c.idx,
       va.proxy_path AS v_proxy, c.v_start_s, c.v_end_s,
       coalesce(aa.proxy_path, aa.path, va.proxy_path) AS a_proxy,
       coalesce(c.a_start_s, c.v_start_s) AS a_start_s,
       coalesce(c.a_end_s, c.v_end_s)     AS a_end_s,
       (c.a_asset_id IS NOT NULL)         AS split_audio,
       c.label, c.note
  FROM media_video.timelines t
  JOIN media_video.timeline_clips c ON c.timeline_id = t.id
  JOIN media.assets va ON va.id = c.v_asset_id
  LEFT JOIN media.assets aa ON aa.id = c.a_asset_id
 ORDER BY t.name, c.idx;
