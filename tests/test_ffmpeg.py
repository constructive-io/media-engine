import tempfile
import unittest
from pathlib import Path

from constructive_media import ffmpeg

from .support import make_still, make_tone, make_video, needs_ffmpeg


class RateTest(unittest.TestCase):
    def test_frame_rates_are_fractions(self):
        self.assertAlmostEqual(ffmpeg._rate("30000/1001"), 29.97, places=2)
        self.assertEqual(ffmpeg._rate("25/1"), 25.0)
        for unusable in ("0/0", "", None, "n/a"):
            self.assertIsNone(ffmpeg._rate(unusable))

    def test_a_missing_program_says_what_to_install(self):
        with self.assertRaises(ffmpeg.FfmpegError) as raised:
            ffmpeg.run("no-such-program-anywhere", "-version")
        self.assertIn("install ffmpeg", str(raised.exception))


@needs_ffmpeg
class ProbeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.folder.cleanup)
        root = Path(cls.folder.name)
        cls.video = make_video(root / "three.mp4")
        cls.tone = make_tone(root / "tone.wav")
        cls.still = make_still(root / "card.png")

    def test_a_video_reports_its_picture_and_sound(self):
        found = ffmpeg.probe(self.video)
        self.assertEqual((found.medium, found.width, found.height), ("video", 160, 90))
        self.assertAlmostEqual(found.fps, 25.0, places=3)
        self.assertAlmostEqual(found.duration_s, 3.0, delta=0.15)
        self.assertTrue(found.has_audio)

    def test_sound_alone_is_audio(self):
        found = ffmpeg.probe(self.tone)
        self.assertEqual((found.medium, found.width, found.has_audio), ("audio", None, True))
        self.assertAlmostEqual(found.duration_s, 0.5, delta=0.05)

    def test_what_is_not_media_is_none(self):
        note = Path(self.folder.name, "note.txt")
        note.write_text("not media")
        self.assertIsNone(ffmpeg.probe(note))
        self.assertIsNone(ffmpeg.probe(Path(self.folder.name, "absent.mp4")))

    def test_one_frame_is_read_at_a_time_and_size(self):
        raw = ffmpeg.frame(self.video, 0.5)
        self.assertEqual(len(raw), 160 * 90 * 3)
        red, green, blue = raw[0], raw[1], raw[2]
        self.assertGreater(red, 200)
        self.assertLess(max(green, blue), 60)
        self.assertEqual(len(ffmpeg.frame(self.video, 2.5, pix_fmt="gray", size=(32, 18))), 32 * 18)

    def test_a_frame_can_be_saved_while_it_is_read(self):
        still = Path(self.folder.name, "stills", "frame.jpg")
        raw = ffmpeg.frame(self.video, 1.5, pix_fmt="gray", still=still)
        self.assertEqual(len(raw), 160 * 90)
        self.assertEqual(ffmpeg.probe(still).width, 160)

    def test_past_the_end_there_is_no_frame(self):
        self.assertEqual(ffmpeg.frame(self.video, 60.0), b"")

    def test_frames_arrive_in_order_at_a_steady_rate(self):
        frames = list(ffmpeg.frames(self.video, fps=10, size=(16, 8)))
        self.assertAlmostEqual(len(frames), 30, delta=1)
        self.assertTrue(all(len(frame) == 16 * 8 * 3 for frame in frames))
        self.assertGreater(frames[2][0], 200)       # red first
        self.assertGreater(frames[-3][2], 150)      # blue last

    def test_an_encode_falls_through_to_an_encoder_that_works(self):
        target = Path(self.folder.name, "copy.mp4")
        used = ffmpeg.encode("-i", self.video, "-c:v", "{vcodec}", "-an", target,
                             video_codecs=["no_such_encoder", *ffmpeg.h264_candidates()])
        self.assertNotEqual(used, "no_such_encoder")
        self.assertEqual(ffmpeg.probe(target).medium, "video")
        with self.assertRaises(ffmpeg.FfmpegError):
            ffmpeg.encode("-i", self.video, "-c:v", "{vcodec}", target, video_codecs=["no_such_encoder"])

    def test_the_fallback_encoder_is_always_a_candidate(self):
        self.assertEqual(ffmpeg.h264_candidates()[-1], ffmpeg.FALLBACK_ENCODER)
        self.assertIn("aac", ffmpeg.encoders())


if __name__ == "__main__":
    unittest.main()
