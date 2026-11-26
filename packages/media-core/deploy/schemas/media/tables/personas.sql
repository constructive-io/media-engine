-- Deploy schemas/media/tables/personas to pg

-- requires: schemas/media

-- The entity every medium is actually about.
--
-- Synthesis and recognition each arrive at this table on their own: a voice
-- tool calls it personas and attaches synthesis voices to it; an editing tool
-- calls it people and attaches recognised voice clusters. They are the same
-- thing seen from opposite directions — one generates a person speaking, the
-- other identifies one who spoke — so it lives once, here, and both domains
-- reference it.
--
-- kind distinguishes a real person from an invented character, because a
-- documentary subject and a narrator persona want the same columns and very
-- different treatment.
CREATE TABLE media.personas (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug          text NOT NULL UNIQUE,
  name          text NOT NULL,
  kind          text NOT NULL DEFAULT 'person'
                CHECK (kind IN ('person','character')),
  bio           text,
  style_summary text,
  tags          text[] NOT NULL DEFAULT '{}',
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX personas_tags_idx ON media.personas USING gin (tags);

-- What a persona was doing in a particular piece of work. The same human can be
-- the interviewer in one film and the subject of the next, so the role belongs
-- on the relationship rather than on the person.
CREATE TABLE media.persona_roles (
  persona_id uuid NOT NULL REFERENCES media.personas(id) ON DELETE CASCADE,
  project    text NOT NULL,
  role       text NOT NULL,          -- subject | interviewer | narrator | crowd
  PRIMARY KEY (persona_id, project, role)
);
