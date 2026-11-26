-- Deploy schemas/media_video/tables/generations to pg

-- requires: schemas/media_video/tables/laughter

-- Video that was GENERATED rather than filmed.
--
-- Everything else in this database describes footage somebody shot: a passage
-- points at a real second of a real recording, and the whole editorial layer
-- assumes that. Generative video breaks that assumption, so it gets its own
-- table rather than a flag tucked into assets.
--
-- ## Why the provenance matters more here than anywhere else
--
-- A generated clip is indistinguishable from footage once it is an mp4 in a
-- folder. Mixed into a documentary cut it becomes a claim that something
-- happened, and nothing downstream — scoring, embedding, export — can tell the
-- difference. So synthesis is recorded as a RELATIONSHIP, not an attribute: an
-- asset is synthetic exactly when it is some generation's output, which cannot
-- be forgotten when a row is copied or a file is renamed. v_synthetic_in_timeline
-- then makes "does this cut contain anything that was not filmed" a query.
--
-- ## Why request_key exists
--
-- These calls cost real money — roughly $0.08/second at 768P and $0.13 at 2K,
-- so a fifteen-second 2K clip is about two dollars. A retried script that
-- re-submits the same prompt pays twice and produces a second, near-identical
-- asset. The key is a digest of everything that determines the output, so
-- asking for the same thing twice returns the first result instead of buying
-- another one. This is the same lesson as deterministic chunk ids: identity
-- derived from content survives a rebuild, identity from a sequence does not.
--
-- ## Status is the provider's, not ours
--
-- Generation is asynchronous — submit, poll, download — and the job outlives
-- the process that started it. Keeping provider_job_id and status here means an
-- interrupted run is resumable rather than lost, and a job that succeeded after
-- we stopped watching is still collectable.
CREATE TABLE media_video.generations (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  model           text NOT NULL,
  backend         text NOT NULL,
  prompt          text NOT NULL,
  -- Everything that shapes the output: duration_s, resolution, ratio, seed.
  params          jsonb NOT NULL DEFAULT '{}'::jsonb,
  -- Conditioning input for image- or video-to-video. A generation derived from
  -- real footage is doubly worth tracking: it looks like that footage.
  input_asset_id  uuid REFERENCES media.assets(id) ON DELETE SET NULL,
  request_key     text NOT NULL UNIQUE,
  status          text NOT NULL DEFAULT 'queued',
  provider_job_id text,
  output_asset_id uuid REFERENCES media.assets(id) ON DELETE SET NULL,
  -- Recorded per generation rather than summed at the end, because the point of
  -- knowing is deciding whether to press go again.
  cost_usd        numeric(10,4),
  error           text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT generations_status_check
    CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
  -- A success with nothing to show for it is a bookkeeping error, and it would
  -- read as "we have this clip" to everything downstream.
  CONSTRAINT generations_succeeded_has_output
    CHECK (status <> 'succeeded' OR output_asset_id IS NOT NULL)
);
CREATE INDEX generations_status_idx ON media_video.generations(status, created_at DESC);
CREATE INDEX generations_output_idx ON media_video.generations(output_asset_id);

-- Which assets were not filmed. Derived, so it cannot drift from the truth.
CREATE VIEW media_video.v_synthetic_assets AS
SELECT a.id AS asset_id,
       substring(a.path from '[^/]+$') AS file,
       g.id AS generation_id, g.model, g.prompt, g.cost_usd, g.created_at
  FROM media_video.generations g
  JOIN media.assets a ON a.id = g.output_asset_id
 WHERE g.status = 'succeeded';

-- The question worth asking before anything ships: does this cut contain
-- material nobody filmed? Covers both picture and sound, since a generation
-- carries its own audio and either track can be the synthetic one.
CREATE VIEW media_video.v_synthetic_in_timeline AS
SELECT t.name AS timeline, c.idx, s.file, s.model, s.prompt,
       CASE WHEN c.v_asset_id = s.asset_id THEN 'picture' ELSE 'sound' END AS track
  FROM media_video.timeline_clips c
  JOIN media_video.timelines t ON t.id = c.timeline_id
  JOIN media_video.v_synthetic_assets s
    ON s.asset_id = c.v_asset_id OR s.asset_id = c.a_asset_id
 ORDER BY t.name, c.idx;

-- What has been spent, by model. Cheap to ask, and the answer is the reason to
-- keep cost on the row at all.
CREATE VIEW media_video.v_generation_spend AS
SELECT model, backend, status,
       count(*) AS runs,
       round(sum(coalesce(cost_usd,0)), 2) AS usd,
       round(sum(coalesce((params->>'duration_s')::numeric,0)), 1) AS seconds
  FROM media_video.generations
 GROUP BY model, backend, status;
