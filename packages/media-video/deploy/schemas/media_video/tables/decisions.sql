-- Deploy schemas/media_video/tables/decisions to pg

-- requires: schemas/media_video/tables/timelines

-- The training set, and the reason any of this uses a database.
--
-- A rendered timeline shows what was chosen; it cannot show what was passed
-- over, and the pair is what a preference model learns from. Recording only the
-- winners gives a training set with no negative class.
CREATE TABLE media_video.decisions (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind          text NOT NULL CHECK (kind IN ('shot_pref','shot_keep','segment_keep','cut_point')),
  chosen_shot   uuid REFERENCES media_video.shots(id) ON DELETE SET NULL,
  rejected_shot uuid REFERENCES media_video.shots(id) ON DELETE SET NULL,
  segment_id    uuid REFERENCES media_video.segments(id) ON DELETE SET NULL,
  keep          boolean,
  context       text,
  actor         text NOT NULL DEFAULT current_user,
  source_of     text NOT NULL DEFAULT 'human'
                CHECK (source_of IN ('human','model','baseline')),
  decided_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX decisions_kind_idx ON media_video.decisions(kind);
CREATE INDEX decisions_actor_idx ON media_video.decisions(actor, decided_at DESC);
