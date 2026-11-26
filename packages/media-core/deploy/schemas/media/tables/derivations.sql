-- Deploy schemas/media/tables/derivations to pg

-- requires: schemas/media/tables/assets

-- What came from what.
--
-- Everything upstream of here was already mapped: a shot knows its asset and its
-- in/out points, a word knows its second, a clip knows both the picture it cuts
-- and the sound it lays under. The gap was the other end. Renders, proxies and
-- extracted snippets existed only as files in work/, so the chain ran
-- source -> analysis -> decision -> (nothing). You could ask what a cut was made
-- of only by reading the code that made it.
--
-- A render is media. It has a duration and a codec, you may want to transcribe
-- it, and you will eventually cut from a cut. So an output is an ordinary row in
-- media.assets, and this table is the edge that says where it came from — rather
-- than a parallel "outputs" table that would need its own probe, its own hash
-- and its own copy of every column assets already has.
--
-- The edge, not the file, is the valuable part. Files in work/ are disposable by
-- design; what must survive is "this mp3 is take harbour-2 at 352.0s as heard
-- by TX01, extracted by the engine on this date". Given that, the file can be
-- deleted and rebuilt.
CREATE TABLE media.derivations (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  output_id     uuid NOT NULL REFERENCES media.assets(id) ON DELETE CASCADE,
  kind          text NOT NULL CHECK (kind IN
                  ('proxy','render','snippet','stem','still','export')),
  -- How to make this file again. `tool` is the verb a human would run; `params`
  -- is whatever that verb needs. Deliberately jsonb: a snippet is identified by
  -- a take and a time, a render by a timeline, a proxy by nothing at all, and
  -- forcing those into shared columns would give every row a column it does not
  -- use plus a migration every time a new output kind appears.
  tool          text NOT NULL,
  params        jsonb NOT NULL DEFAULT '{}',
  -- Times are in the OUTPUT's own clock and say which stretch of the take or
  -- source it represents. Null for whole-file derivations such as proxies.
  start_s       double precision,
  end_s         double precision,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX derivations_output_idx ON media.derivations(output_id);
CREATE INDEX derivations_kind_idx   ON media.derivations(kind);

-- Which source assets went into it, and which part of each.
--
-- Many-to-many because that is what editing is: a seven-cut render draws on
-- three cameras and a lav. Storing it as a single parent_id would force a
-- render to claim one source and lose the rest, which is exactly the question
-- worth asking — "what have I already used from this interview?"
CREATE TABLE media.derivation_sources (
  derivation_id uuid NOT NULL REFERENCES media.derivations(id) ON DELETE CASCADE,
  source_id     uuid NOT NULL REFERENCES media.assets(id) ON DELETE CASCADE,
  role          text,                  -- 'picture', 'sound', 'reference'
  -- The span used, in the SOURCE's own clock — the same convention as
  -- media_video.shots, so these join without conversion. Null means the whole
  -- file.
  start_s       double precision,
  end_s         double precision,
  PRIMARY KEY (derivation_id, source_id, role)
);
CREATE INDEX derivation_sources_source_idx ON media.derivation_sources(source_id);

-- Provenance in both directions, which is the whole point of recording it.
CREATE VIEW media.v_derivations AS
SELECT d.id,
       d.kind,
       d.tool,
       o.path                                   AS output_path,
       o.duration_s                             AS output_duration_s,
       d.start_s, d.end_s,
       d.params,
       d.created_at,
       count(s.source_id)                       AS n_sources,
       array_agg(DISTINCT substring(src.path from '[^/]+$')
                 ORDER BY substring(src.path from '[^/]+$')) AS sources
  FROM media.derivations d
  JOIN media.assets o        ON o.id = d.output_id
  LEFT JOIN media.derivation_sources s ON s.derivation_id = d.id
  LEFT JOIN media.assets src ON src.id = s.source_id
 GROUP BY d.id, d.kind, d.tool, o.path, o.duration_s, d.start_s, d.end_s,
          d.params, d.created_at;
