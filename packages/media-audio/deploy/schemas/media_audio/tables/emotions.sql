-- Deploy schemas/media_audio/tables/emotions to pg

-- requires: schemas/media_audio

-- The delivery vocabulary. A closed set on purpose: a free-text emotion column
-- becomes forty spellings of "warm" within a month, and every voice is keyed by
-- (persona, emotion, engine).
CREATE TABLE media_audio.emotions (
  slug        text PRIMARY KEY,
  label       text NOT NULL,
  description text,
  sort_order  integer NOT NULL DEFAULT 0
);
INSERT INTO media_audio.emotions (slug,label,description,sort_order) VALUES
  ('neutral','Neutral','Grounded, even baseline delivery. The fallback for any untrained emotion.',0),
  ('warm','Warm','Affectionate, encouraging, close.',1),
  ('excited','Excited','High-energy, animated, bright.',2),
  ('tender','Tender','Soft, gentle, intimate.',3),
  ('whisper','Whisper','Hushed, breathy, confidential.',4),
  ('serious','Serious','Measured, weighty, deliberate.',5),
  ('playful','Playful','Light, teasing, quick.',6)
ON CONFLICT (slug) DO NOTHING;
