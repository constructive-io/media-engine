-- Deploy schemas/media_video/tables/styles to pg

-- requires: schemas/media_video/views/shot_content

-- The kind of edit this is, as a first-class thing.
--
-- A cut is not just a selection, it is a selection made in a manner. The same
-- interview yields a fast, filler-stripped, jump-cut version and a slow
-- thematic one, and the difference is not a parameter tweak — it is a different
-- set of decisions about pace, length, cutaway rate and how much dead air is
-- tolerable. Storing the style makes runs comparable ("show me every tiktok cut
-- of this episode") and makes feedback attributable to a manner of editing
-- rather than to a single output.
CREATE TABLE media_video.styles (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name          text NOT NULL UNIQUE,
  description   text,
  -- Pace and shape. These are the knobs that actually differ between an edit
  -- built for someone scrolling and one built for someone watching.
  params        jsonb NOT NULL DEFAULT '{}',
  created_at    timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE media_video.timelines
  ADD COLUMN style_id uuid REFERENCES media_video.styles(id) ON DELETE SET NULL,
  -- Where this run sits in the chronology of attempts on an episode. Distinct
  -- from `revision`, which counts edits to one lineage: two styles run on the
  -- same episode are both attempt 1 of their own line but sit side by side in
  -- the episode's history.
  ADD COLUMN run_at timestamptz NOT NULL DEFAULT now();
CREATE INDEX timelines_style_idx ON media_video.timelines(style_id, run_at DESC);

ALTER TABLE media_video.selectors
  ADD COLUMN style_id uuid REFERENCES media_video.styles(id) ON DELETE SET NULL;

-- Two starting styles, with the numbers that make them different rather than
-- names that only imply it.
INSERT INTO media_video.styles (name, description, params) VALUES
  ('tiktok',
   'Fast, filler-stripped, high cutaway rate. Built for someone scrolling.',
   '{"target_s": 60, "max_passage_s": 14, "min_passage_s": 4,
     "strip_fillers": true, "strip_silence_over_s": 0.35,
     "broll_fraction": 0.5, "chronological": false, "min_words": 25}'),
  ('thematic',
   'Slower, whole answers, sparing cutaways. Built for someone watching.',
   '{"target_s": 420, "max_passage_s": 95, "min_passage_s": 20,
     "strip_fillers": false, "strip_silence_over_s": 1.5,
     "broll_fraction": 0.25, "chronological": true, "min_words": 80}')
ON CONFLICT (name) DO NOTHING;

-- Every run on an episode, newest first: the chronology.
CREATE VIEW media_video.v_runs AS
SELECT e.slug                AS episode,
       t.name                AS timeline,
       s.name                AS style,
       t.revision,
       t.status,
       t.seed,
       t.run_at,
       sel.name              AS selector,
       count(c.id)           AS clips,
       round(sum(coalesce(c.a_end_s, c.v_end_s)
                 - coalesce(c.a_start_s, c.v_start_s))::numeric, 1) AS duration_s,
       count(n.id) FILTER (WHERE n.verdict = 'drop')   AS dropped,
       count(n.id) FILTER (WHERE n.verdict = 'adjust') AS adjusted
  FROM media_video.timelines t
  LEFT JOIN media.episodes e          ON e.id = t.episode_id
  LEFT JOIN media_video.styles s      ON s.id = t.style_id
  LEFT JOIN media_video.selectors sel ON sel.id = t.selector_id
  LEFT JOIN media_video.timeline_clips c ON c.timeline_id = t.id
  LEFT JOIN media_video.clip_notes n   ON n.clip_id = c.id
 GROUP BY e.slug, t.name, s.name, t.revision, t.status, t.seed, t.run_at, sel.name;
