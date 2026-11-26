-- Deploy schemas/media/tables/episodes to pg

-- requires: schemas/media/tables/takes

-- The thing being made, above the material it is made from.
--
-- `assets.project` was a text label, which is enough to filter a query and not
-- enough to be a unit of work: it cannot carry a running order, a status, a
-- logline, or the fact that this episode draws on three shoots and a piece of
-- archive.
--
-- Takes already group files that share a clock — a camera with its own audio,
-- three lavs of different lengths, one primary. An episode groups TAKES, plus
-- loose assets that belong to it without belonging to any take, which is what
-- b-roll is.
CREATE TABLE media.episodes (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug        text NOT NULL UNIQUE,
  title       text,
  logline     text,
  project     text,
  status      text NOT NULL DEFAULT 'assembly'
              CHECK (status IN ('assembly','review','locked','released')),
  target_s    double precision,      -- how long it is meant to run
  notes       text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Running order. `ord` is the intended sequence of material, which is a
-- different thing from any particular timeline's cut of it: the order you
-- planned survives every regeneration that reshuffles the cut.
CREATE TABLE media.episode_takes (
  episode_id  uuid NOT NULL REFERENCES media.episodes(id) ON DELETE CASCADE,
  take_id     uuid NOT NULL REFERENCES media.takes(id) ON DELETE CASCADE,
  ord         integer,
  role        text,                  -- 'interview', 'performance', 'b-roll'
  PRIMARY KEY (episode_id, take_id)
);

-- Assets that belong to an episode without belonging to a take. A drone shot
-- shares no clock with anything and still belongs to the episode.
CREATE TABLE media.episode_assets (
  episode_id  uuid NOT NULL REFERENCES media.episodes(id) ON DELETE CASCADE,
  asset_id    uuid NOT NULL REFERENCES media.assets(id) ON DELETE CASCADE,
  role        text,
  PRIMARY KEY (episode_id, asset_id)
);

-- Everything an episode can draw on, takes and loose assets alike, in one place.
CREATE VIEW media.v_episode_sources AS
SELECT e.slug AS episode, 'take'::text AS via, t.name AS source,
       et.role, et.ord, a.id AS asset_id, a.path, a.medium, a.duration_s
  FROM media.episodes e
  JOIN media.episode_takes et ON et.episode_id = e.id
  JOIN media.takes t          ON t.id = et.take_id
  JOIN media.take_assets ta   ON ta.take_id = t.id
  JOIN media.assets a         ON a.id = ta.asset_id
UNION ALL
SELECT e.slug, 'asset', NULL, ea.role, NULL, a.id, a.path, a.medium, a.duration_s
  FROM media.episodes e
  JOIN media.episode_assets ea ON ea.episode_id = e.id
  JOIN media.assets a          ON a.id = ea.asset_id;
