-- Deploy schemas/media_video/tables/embeddings to pg

-- requires: schemas/media_video/tables/scores

-- Ask the footage a question in words.
--
-- Every other way of finding material here needs you to already know where it
-- is: a take name, a time, a regex over a path. The one question an editor
-- actually asks — "where does anyone talk about cooling the data centre" —
-- had no answer, because it is about MEANING and nothing stored meaning.
--
-- An embedding per passage makes that a query. And because a passage already
-- carries its take, its times and its devices, a semantic hit is not a search
-- result: it is a cut, with in and out points, ready to become an EDL.
--
-- That is the whole point of putting this in the database rather than in a
-- vector store beside it. `ORDER BY embedding <=> query` in the same statement
-- that joins takes, offsets, coherence and exclusions means "find me the good
-- bits about robots that I have not already rejected" is one query rather than
-- three systems agreeing.
CREATE TABLE media_video.passage_embeddings (
  passage_id  uuid PRIMARY KEY REFERENCES media_video.passages(id) ON DELETE CASCADE,
  -- qwen3-embedding:0.6b. Dimension is fixed by the model, so changing model
  -- means a new column or a new table, never a silent reinterpretation of
  -- numbers that no longer mean the same thing.
  embedding   vector(1024) NOT NULL,
  model       text NOT NULL,
  -- What was embedded. The text changes when a transcript is corrected, and an
  -- embedding of superseded words is worse than none: it still answers, just
  -- about something nobody said.
  text_hash   text NOT NULL,
  computed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX passage_embeddings_ann ON media_video.passage_embeddings
  USING hnsw (embedding vector_cosine_ops);

-- Everything needed to turn a semantic hit into a cut, in one place.
CREATE VIEW media_video.v_searchable AS
SELECT p.id AS passage_id, e.embedding, p.take_id, t.name AS take,
       t.project, p.start_s, p.end_s, p.end_s - p.start_s AS duration_s,
       p.text, p.n_words, p.method,
       coalesce(q.coherence, 0.5) AS coherence,
       coalesce(q.ends_complete, false) AS ends_complete,
       EXISTS (SELECT 1 FROM media_video.exclusions x
                WHERE x.passage_id = p.id) AS excluded
  FROM media_video.passages p
  JOIN media_video.passage_embeddings e ON e.passage_id = p.id
  JOIN media.takes t ON t.id = p.take_id
  LEFT JOIN media_video.passage_quality q ON q.passage_id = p.id;
