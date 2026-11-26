-- Deploy schemas/media_video/tables/asr_model to pg

-- requires: schemas/media_video/tables/corrections

-- Which recogniser produced this, so a tier can be re-run and compared.
--
-- Transcription is a tier like any other: `base` runs over everything at 42x
-- realtime, and the expensive model runs only where the cheap one struggled.
-- That only works if each segment records what made it — otherwise "has this
-- already been upgraded" is unanswerable and the expensive pass either repeats
-- itself or is skipped entirely.
--
-- Measured on the worst 120 seconds of one recording: base returned 6 segments at
-- mean logprob -0.59, small returned 57 at -0.44. base was not mis-hearing so
-- much as NOT HEARING — it skipped most of the speech, which is invisible in a
-- transcript and fatal to a search over one.
ALTER TABLE media_video.segments ADD COLUMN model text;
UPDATE media_video.segments SET model = 'base' WHERE model IS NULL;
CREATE INDEX segments_model_idx ON media_video.segments(model);

-- Where the cheap tier struggled, per asset.
CREATE VIEW media_video.v_asr_quality AS
SELECT a.id AS asset_id,
       substring(a.path from '[^/]+$') AS file,
       a.project,
       count(*)                                              AS segments,
       min(s.model)                                          AS model,
       round(avg(s.avg_logprob)::numeric, 3)                 AS mean_logprob,
       count(*) FILTER (WHERE s.avg_logprob < -1.2)          AS suspect,
       round((count(*) FILTER (WHERE s.avg_logprob < -1.2))::numeric
             / nullif(count(*), 0), 3)                       AS suspect_ratio,
       -- Speech found per minute of recording. A recogniser that skips speech
       -- looks confident about the little it kept, so density catches what
       -- confidence alone cannot.
       round((count(*) / nullif(a.duration_s / 60.0, 0))::numeric, 1)
                                                             AS segs_per_min
  FROM media_video.segments s
  JOIN media.assets a ON a.id = s.asset_id
 WHERE s.avg_logprob IS NOT NULL
 GROUP BY a.id, a.path, a.project, a.duration_s;
