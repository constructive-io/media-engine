-- Deploy schemas/media_video/tables/generations_medium to pg

-- requires: schemas/media_video/tables/generations

-- Generations are not only video.
--
-- The workflow that made this necessary: generate stills, agree on one, then
-- animate the chosen still. The image is not a throwaway intermediate — it is
-- the thing a human approved, and the video's provenance is meaningless without
-- it. `input_asset_id` already pointed a generation at its source asset, so a
-- chain image -> video is expressible; what was missing was being able to ask
-- for the images on their own.
--
-- Recorded explicitly rather than inferred from the output asset's medium,
-- because the medium is known when the request is MADE and the output does not
-- exist until it succeeds. Inferring it would leave every queued row unanswerable.
ALTER TABLE media_video.generations
  ADD COLUMN medium text NOT NULL DEFAULT 'video';

ALTER TABLE media_video.generations
  ADD CONSTRAINT generations_medium_check
  CHECK (medium IN ('video','image','audio'));

CREATE INDEX generations_medium_idx ON media_video.generations(medium, created_at DESC);

-- The chain, as one row per generated video: which still it came from, who
-- approved that still by choosing it, and what both were asked for.
CREATE VIEW media_video.v_generation_chain AS
SELECT v.id AS video_id, v.model AS video_model, v.prompt AS video_prompt,
       v.status, v.cost_usd,
       i.id AS from_image_id, i.model AS image_model, i.prompt AS image_prompt,
       substring(src.path from '[^/]+$') AS image_file,
       substring(out.path from '[^/]+$') AS video_file
  FROM media_video.generations v
  LEFT JOIN media.assets src ON src.id = v.input_asset_id
  LEFT JOIN media_video.generations i ON i.output_asset_id = v.input_asset_id
  LEFT JOIN media.assets out ON out.id = v.output_asset_id
 WHERE v.medium = 'video';
