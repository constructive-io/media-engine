-- Deploy schemas/media_audio/tables/review to pg

-- requires: schemas/media_audio/tables/renders

-- Human judgement on generated audio.
--
-- The same argument as the video decisions table: this is the part that cannot
-- be regenerated from the media. A render can always be redone; the knowledge
-- that a particular take was wrong, and why, exists only if it was written down.
CREATE TABLE media_audio.review_queue (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  render_id     uuid REFERENCES media_audio.renders(id) ON DELETE CASCADE,
  render_label  text,
  line_no       integer NOT NULL,
  persona_id    uuid REFERENCES media.personas(id) ON DELETE SET NULL,
  emotion       text,
  intended_text text NOT NULL,
  spoken_text   text,
  heard_text    text,
  word_error    numeric,
  char_sim      numeric,
  attempts      integer NOT NULL DEFAULT 1,
  audio_path    text,
  verdict       text CHECK (verdict IN ('accept','retry','reject')),
  note          text,
  reviewed_at   timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX review_verdict_idx ON media_audio.review_queue(verdict);
