-- Deploy schemas/media_video/tables/variants to pg

-- requires: schemas/media_video/tables/passages
-- requires: media-core:schemas/media/tables/episodes

-- Timelines you can regenerate, react to, and regenerate again.
--
-- A timeline was a flat name. That is enough to render once and nothing else:
-- you cannot ask which selector produced it, what it was a revision OF, whether
-- two cuts came from the same sources, or why the third attempt was better. The
-- loop an editor wants — generate, watch, blacklist, adjust, regenerate — needs all
-- four.
ALTER TABLE media_video.timelines
  ADD COLUMN episode_id  uuid REFERENCES media.episodes(id) ON DELETE SET NULL,
  ADD COLUMN selector_id uuid REFERENCES media_video.selectors(id) ON DELETE SET NULL,
  ADD COLUMN parent_id   uuid REFERENCES media_video.timelines(id) ON DELETE SET NULL,
  ADD COLUMN revision    integer NOT NULL DEFAULT 1,
  -- The seed makes a "randomly generated" variant reproducible. A cut you liked
  -- and cannot regenerate is a cut you have lost.
  ADD COLUMN seed        bigint,
  ADD COLUMN status      text NOT NULL DEFAULT 'draft'
             CHECK (status IN ('draft','review','approved','discarded'));
CREATE INDEX timelines_episode_idx ON media_video.timelines(episode_id, revision);

-- Reaction to one cut inside one timeline. The video counterpart of
-- media_audio.review_queue, and deliberately the same shape: a verdict, a note,
-- and a record of when a human looked.
--
-- `shift_s` and `extend_s` exist because "this cut is right but late" is the
-- most common note an editor gives, and turning it into a rejection loses the
-- information that the SELECTION was correct.
CREATE TABLE media_video.clip_notes (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  clip_id      uuid NOT NULL REFERENCES media_video.timeline_clips(id) ON DELETE CASCADE,
  verdict      text NOT NULL CHECK (verdict IN ('keep','drop','adjust')),
  shift_s      double precision,
  extend_s     double precision,
  note         text,
  actor        text NOT NULL DEFAULT current_user,
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX clip_notes_clip_idx ON media_video.clip_notes(clip_id);

-- Never use this again.
--
-- Scoped to the SOURCE, not to a timeline, which is the whole point: a blacklist
-- attached to one cut dies the moment you regenerate. Attached to the shot or
-- passage, it survives every regeneration, which is what "I don't want to see
-- that again" actually means.
--
-- `episode_id` null means globally excluded; set, means excluded from this
-- episode only — a shot can be wrong for this story and fine for another.
CREATE TABLE media_video.exclusions (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  shot_id     uuid REFERENCES media_video.shots(id) ON DELETE CASCADE,
  passage_id  uuid REFERENCES media_video.passages(id) ON DELETE CASCADE,
  asset_id    uuid REFERENCES media.assets(id) ON DELETE CASCADE,
  episode_id  uuid REFERENCES media.episodes(id) ON DELETE CASCADE,
  reason      text,
  actor       text NOT NULL DEFAULT current_user,
  created_at  timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT exclusions_target CHECK (
    num_nonnulls(shot_id, passage_id, asset_id) = 1)
);
CREATE INDEX exclusions_shot_idx ON media_video.exclusions(shot_id);
CREATE INDEX exclusions_passage_idx ON media_video.exclusions(passage_id);

-- What a generator must skip, resolved for one episode.
CREATE VIEW media_video.v_excluded_shots AS
SELECT DISTINCT x.shot_id, x.episode_id, x.reason
  FROM media_video.exclusions x WHERE x.shot_id IS NOT NULL
UNION
SELECT sh.id, x.episode_id, x.reason
  FROM media_video.exclusions x
  JOIN media_video.shots sh ON sh.asset_id = x.asset_id
 WHERE x.asset_id IS NOT NULL;

-- One row per cut with its latest reaction, which is what a review pass reads.
CREATE VIEW media_video.v_timeline_review AS
SELECT t.name AS timeline, t.revision, t.status, c.idx, c.id AS clip_id,
       c.label, c.v_start_s, c.v_end_s,
       n.verdict, n.shift_s, n.extend_s, n.note, n.created_at AS noted_at
  FROM media_video.timelines t
  JOIN media_video.timeline_clips c ON c.timeline_id = t.id
  LEFT JOIN LATERAL (
        SELECT * FROM media_video.clip_notes cn
         WHERE cn.clip_id = c.id ORDER BY cn.created_at DESC LIMIT 1) n ON true;
