-- Verify schemas/media_video/tables/audio_energy on pg

SELECT loudness, peak_db, lift_db FROM media_video.shots WHERE false;
