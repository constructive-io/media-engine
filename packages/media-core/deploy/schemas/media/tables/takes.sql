-- Deploy schemas/media/tables/takes to pg

-- requires: schemas/media/tables/assets
-- requires: schemas/media/tables/personas

-- A take is one event recorded by several devices.
--
-- Until now every file was an island: four lav recorders and three camera files
-- covering the same twenty minutes were seven unrelated assets, with no way to
-- say they were the same moment. That makes three things impossible — using the
-- good audio with the good picture, knowing which mic belongs to which person,
-- and choosing between cameras at a given instant.
--
-- Devices do not share a clock. The recorders here rolled continuously for 30
-- minutes while the camera started and stopped, so every asset carries an offset
-- into the take's timeline rather than being assumed to start at zero.
CREATE TABLE media.takes (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name       text NOT NULL UNIQUE,
  project    text,
  started_at timestamptz,
  notes      text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE media.take_assets (
  take_id    uuid NOT NULL REFERENCES media.takes(id) ON DELETE CASCADE,
  asset_id   uuid NOT NULL REFERENCES media.assets(id) ON DELETE CASCADE,

  role       text NOT NULL CHECK (role IN ('camera','lav','ambient','board','screen')),

  -- Seconds to ADD to this asset's own time to reach take time. The primary
  -- asset is 0 by definition; everything else is measured against it, normally
  -- by cross-correlating audio.
  offset_s   double precision NOT NULL DEFAULT 0,
  offset_confidence double precision,     -- null until sync has actually run
  is_primary boolean NOT NULL DEFAULT false,

  -- Whose microphone this is. On a lav that is ground truth, not inference:
  -- it makes diarisation exact rather than a pitch guess, and it is the single
  -- most valuable column here.
  persona_id uuid REFERENCES media.personas(id) ON DELETE SET NULL,

  label      text,                        -- 'A cam', 'TX01', 'room'
  PRIMARY KEY (take_id, asset_id)
);
CREATE INDEX take_assets_asset_idx ON media.take_assets(asset_id);
CREATE UNIQUE INDEX take_one_primary ON media.take_assets(take_id) WHERE is_primary;

-- Everything in a take, with take-relative times already worked out, so callers
-- never repeat the offset arithmetic and never get its sign wrong.
CREATE VIEW media.v_take_assets AS
SELECT t.name AS take, t.project, ta.role, ta.label, ta.is_primary,
       a.id AS asset_id, a.medium, a.path, a.proxy_path, a.duration_s,
       ta.offset_s,
       ta.offset_s                        AS take_start_s,
       ta.offset_s + a.duration_s         AS take_end_s,
       p.name                             AS persona,
       ta.persona_id
  FROM media.takes t
  JOIN media.take_assets ta ON ta.take_id = t.id
  JOIN media.assets a       ON a.id = ta.asset_id
  LEFT JOIN media.personas p ON p.id = ta.persona_id
 ORDER BY t.name, ta.is_primary DESC, ta.role, ta.label;
