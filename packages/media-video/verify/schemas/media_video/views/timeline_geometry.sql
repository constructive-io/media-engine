-- Verify schemas/media_video/views/timeline_geometry on pg

SELECT timeline, clips, distinct_aspects, aspects, orientations,
       min_width, max_width, narrowest, widest
  FROM media_video.v_timeline_geometry
 WHERE false;
