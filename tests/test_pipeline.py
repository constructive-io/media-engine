"""The engine end to end, against a real database, on media made on the spot."""

import re
import tempfile
import unittest
from pathlib import Path

from constructive_media import db, derive, ingest
from constructive_media.detection import Detection

from .support import (
    HAVE_FFMPEG, HAVE_NUMPY, DatabaseTest, make_still, make_tone, make_video, settings_in,
)

if HAVE_NUMPY:
    from constructive_media.video import analyze


class FakeDetector:
    """Finds two people in the first frame of the first shot, one in its
    others, a dog in the second shot and nothing in the third."""

    name = "fake"
    model = "fake-1"

    def __init__(self):
        self.seen = []

    def detect(self, images, confidence=0.35):
        self.seen += images
        return [self.answer(Path(image).name) for image in images]

    def answer(self, name):
        near = Detection("person", 0.9, 0.5, 0.5, 0.5, 0.6)
        far = Detection("person", 0.6, 0.2, 0.5, 0.1, 0.1)
        if name.startswith("00000_"):
            first = name == min(Path(p).name for p in self.seen if Path(p).name.startswith("00000_"))
            return [near, far] if first else [near]
        if name.startswith("00001_"):
            return [Detection("dog", 0.8, 0.5, 0.5, 0.2, 0.2)]
        return []


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg is not installed")
class LibraryTest(DatabaseTest):
    @classmethod
    def setUpClass(cls):
        folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(folder.cleanup)
        cls.root = Path(folder.name).resolve()
        cls.footage = cls.root / "footage"
        (cls.footage / "exports").mkdir(parents=True)
        cls.video = make_video(cls.footage / "three.mp4")
        make_video(cls.footage / "exports" / "cut.mp4", colours=("white", "black"))
        make_tone(cls.footage / "tone.wav")
        make_still(cls.footage / "card.png")
        (cls.footage / "broken.mp4").write_bytes(b"this is not a video")
        (cls.footage / "notes.txt").write_text("not media")
        cls.settings = settings_in(cls.root / "home")
        cls.match = re.escape(str(cls.footage))

    def ingest(self, project="demo"):
        return ingest.ingest(self.footage, project, settings=self.settings)

    def test_a_folder_is_registered_file_by_file(self):
        results = {result.name: result.state for result in self.ingest()}
        self.assertEqual(results, {
            "broken.mp4": "unreadable", "card.png": "ingested",
            "three.mp4": "ingested", "tone.wav": "ingested",
        })
        rows = self.query(
            """SELECT medium, project, width, has_audio, proxy_path, path
                 FROM media.assets WHERE path ~ %s ORDER BY path""", (self.match,))
        self.assertEqual([row[0] for row in rows], ["image", "video", "audio"])
        self.assertEqual({row[1] for row in rows}, {"demo"})
        _medium, _project, width, has_audio, proxy, path = rows[1]
        self.assertEqual((width, has_audio, path), (160, True, str(self.video)))
        self.assertTrue(Path(proxy).is_file())
        self.assertIn(self.settings.work_dir, Path(proxy).parents)

    def test_ingesting_again_changes_nothing(self):
        first = {result.name: result.asset_id for result in self.ingest(project=None)}
        again = self.ingest(project="later")
        self.assertEqual({r.state for r in again if r.name != "broken.mp4"}, {"have"})
        self.assertEqual({r.name: r.asset_id for r in again}, first)
        projects = self.query("SELECT DISTINCT project FROM media.assets WHERE path ~ %s", (self.match,))
        self.assertEqual(projects, [("later",)])
        self.ingest(project="renamed")
        projects = self.query("SELECT DISTINCT project FROM media.assets WHERE path ~ %s", (self.match,))
        self.assertEqual(projects, [("later",)])

    def test_a_derived_file_knows_what_it_was_made_from(self):
        by_name = {result.name: result.asset_id for result in self.ingest()}
        output = make_tone(self.root / "voiceover.wav")
        self.assertIsNone(derive.lineage(output))

        derive.record(output, "render", "tests", [
            derive.Source(by_name["three.mp4"], start_s=1.0, end_s=2.0),
            derive.Source(by_name["tone.wav"], role="music"),
            derive.Source(None),
        ], params={"gain": -3, "tags": ["a", "b"]}, start_s=0.0, end_s=0.5, project="demo")
        found = derive.lineage(output)
        self.assertEqual((found.kind, found.tool), ("render", "tests"))
        self.assertEqual(found.params, {"gain": -3, "tags": ["a", "b"]})
        self.assertEqual(sorted(found.sources), ["three.mp4", "tone.wav"])
        self.assertEqual(self.query("SELECT medium FROM media.assets WHERE path = %s", (str(output),)),
                         [("audio",)])

    def test_recording_a_file_again_replaces_its_record(self):
        by_name = {result.name: result.asset_id for result in self.ingest()}
        output = make_tone(self.root / "replaced.wav")
        first = derive.record(output, "render", "first", [derive.Source(by_name["three.mp4"])])
        make_tone(output, seconds=0.8)
        second = derive.record(output, "export", "second", [derive.Source(by_name["tone.wav"])])
        self.assertEqual(first, second)
        found = derive.lineage(output)
        self.assertEqual((found.kind, found.tool, found.sources), ("export", "second", ("tone.wav",)))
        self.assertEqual(self.query(
            "SELECT count(*) FROM media.derivations WHERE output_id = %s", (first,)), [(1,)])
        self.assertAlmostEqual(self.query(
            "SELECT duration_s FROM media.assets WHERE id = %s", (first,))[0][0], 0.8, delta=0.05)

    def test_a_copy_of_a_source_is_its_own_asset(self):
        by_name = {result.name: result.asset_id for result in self.ingest()}
        copy = self.root / "copy-of-tone.wav"
        copy.write_bytes((self.footage / "tone.wav").read_bytes())
        copied = derive.record(copy, "export", "tests", [derive.Source(by_name["tone.wav"])])
        self.assertNotEqual(copied, by_name["tone.wav"])
        self.assertEqual(derive.lineage(copy).sources, ("tone.wav",))
        self.assertEqual(self.query("SELECT path FROM media.assets WHERE id = %s", (by_name["tone.wav"],)),
                         [(str(self.footage / "tone.wav"),)])
        # Ingesting the folder the copy sits in finds it already known, by its path.
        self.assertEqual(ingest.ingest(copy, settings=self.settings)[0], ingest.Ingested(copy.name, "have", copied))

    def test_an_unknown_kind_of_derivation_is_refused(self):
        with self.assertRaises(ValueError):
            derive.record(self.video, "remix", "tests")


@unittest.skipUnless(HAVE_FFMPEG and HAVE_NUMPY, "ffmpeg and numpy are needed")
class AnalysisTest(DatabaseTest):
    @classmethod
    def setUpClass(cls):
        folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(folder.cleanup)
        cls.root = Path(folder.name).resolve()
        make_video(cls.root / "footage" / "three.mp4")
        make_video(cls.root / "footage" / "night.mp4", colours=("black", "white"))
        cls.settings = settings_in(cls.root / "home")
        cls.match = re.escape(str(cls.root / "footage"))
        cls.three = re.escape(str(cls.root / "footage" / "three.mp4"))

    def setUp(self):
        super().setUp()
        ingest.ingest(self.root / "footage", settings=self.settings)

    def shots(self, columns, match=None):
        return self.query(
            f"""SELECT {columns} FROM media_video.shots s JOIN media.assets a ON a.id = s.asset_id
                 WHERE a.path ~ %s ORDER BY a.path, s.idx""", (match or self.three,))

    def test_shots_are_found_once(self):
        self.assertEqual(sorted(analyze.shots(self.match)), [("night.mp4", 2), ("three.mp4", 3)])
        self.assertEqual(analyze.shots(self.match), [])
        spans = self.shots("s.idx, s.start_s, s.end_s")
        self.assertEqual([index for index, _, _ in spans], [0, 1, 2])
        for (_, start, end), expected in zip(spans, (0.0, 1.0, 2.0)):
            self.assertAlmostEqual(start, expected, delta=0.05)
            self.assertAlmostEqual(end, expected + 1.0, delta=0.1)

    def test_match_narrows_a_stage_to_some_assets(self):
        self.assertEqual(analyze.shots(self.three), [("three.mp4", 3)])
        self.assertEqual(analyze.shots("no-such-footage"), [])

    def test_frames_are_saved_measured_and_summed_into_the_shot(self):
        analyze.shots(self.match)
        self.assertEqual(sorted(analyze.frames(self.match, per_shot=2, settings=self.settings)),
                         [("night.mp4", 4), ("three.mp4", 6)])
        self.assertEqual(analyze.frames(self.match, per_shot=2, settings=self.settings), [])
        frames = self.query(
            """SELECT f.path, f.t_s, f.brightness, s.start_s, s.end_s
                 FROM media_video.frames f JOIN media_video.shots s ON s.id = f.shot_id
                 JOIN media.assets a ON a.id = s.asset_id WHERE a.path ~ %s""", (self.three,))
        self.assertEqual(len(frames), 6)
        for path, t_s, brightness, start, end in frames:
            self.assertTrue(Path(path).is_file())
            self.assertIn(self.settings.work_dir, Path(path).parents)
            self.assertTrue(start < t_s < end)
            self.assertTrue(0 <= brightness <= 255)
        black = self.shots("s.is_black", re.escape(str(self.root / "footage" / "night.mp4")))
        self.assertEqual(black, [(True,), (False,)])
        for sharpness, exposure in self.shots("s.sharpness, s.exposure"):
            self.assertLess(sharpness, 50)        # flat colour has no detail
            self.assertEqual(exposure, 0.0)       # and nothing clipped

    def test_signatures_describe_the_colour_of_each_shot(self):
        analyze.shots(self.three)
        self.assertEqual(analyze.signatures(self.three), 3)
        self.assertEqual(analyze.signatures(self.three), 0)
        rows = self.query(
            """SELECT g.hue, g.value, g.sat_mean, g.motion, g.dominant_h
                 FROM media_video.signatures g JOIN media_video.shots s ON s.id = g.shot_id
                 JOIN media.assets a ON a.id = s.asset_id WHERE a.path ~ %s ORDER BY s.idx""",
            (self.three,))
        for hue, value, _sat, motion, _dominant in rows:
            self.assertEqual((len(hue), len(value)), (analyze.HUE_BINS, analyze.VALUE_BINS))
            self.assertAlmostEqual(sum(hue), 1.0, places=4)
            self.assertAlmostEqual(sum(value), 1.0, places=4)
            self.assertLess(motion, 2.0)          # nothing moves in a flat colour
        red, green, pale = rows
        self.assertIn(red[4], (0.0, 345.0))
        self.assertTrue(105 <= green[4] <= 150)
        self.assertTrue(210 <= pale[4] <= 240)
        self.assertGreater(red[2], 200)           # saturated
        self.assertLess(pale[2], 80)              # washed out

    def test_detections_are_stored_and_people_frame_the_shot(self):
        analyze.shots(self.three)
        analyze.frames(self.three, per_shot=3, settings=self.settings)
        finder = FakeDetector()
        self.assertEqual(analyze.detect(self.three, detector=finder), 9)
        self.assertEqual(len(finder.seen), 9)
        self.assertEqual(analyze.detect(self.three, detector=finder), 0)
        found = self.query(
            """SELECT s.idx, d.class_name, d.model, count(*) FROM media_video.detections d
                 JOIN media_video.frames f ON f.id = d.frame_id
                 JOIN media_video.shots s ON s.id = f.shot_id
                 JOIN media.assets a ON a.id = s.asset_id WHERE a.path ~ %s
                GROUP BY 1, 2, 3 ORDER BY 1, 2""", (self.three,))
        self.assertEqual(found, [(0, "person", "fake-1", 4), (1, "dog", "fake-1", 3)])
        subjects = self.shots("s.subject_n, s.subject_area, s.shot_size")
        self.assertEqual(subjects[0][0], 2)       # the most people in any one frame, not the sum
        self.assertAlmostEqual(subjects[0][1], 0.3)
        self.assertEqual(subjects[0][2], "close")
        self.assertEqual(subjects[1], (0, None, "none"))   # a dog is not a person
        self.assertEqual(subjects[2], (0, None, "none"))

    def test_a_run_that_was_interrupted_is_finished_from_what_is_stored(self):
        analyze.shots(self.three)
        analyze.frames(self.three, per_shot=3, settings=self.settings)
        frame_id = self.query(
            """SELECT f.id FROM media_video.frames f JOIN media_video.shots s ON s.id = f.shot_id
                 JOIN media.assets a ON a.id = s.asset_id
                WHERE a.path ~ %s AND s.idx = 2 ORDER BY f.t_s LIMIT 1""", (self.three,))[0][0]
        db.cursor().execute(
            """INSERT INTO media_video.detections (frame_id, class_name, confidence, cx, cy, w, h, model)
               VALUES (%s, 'person', 0.9, 0.5, 0.5, 0.1, 0.1, 'earlier-run')""", (frame_id,))
        self.assertEqual(analyze.detect(self.three, detector=FakeDetector()), 8)
        self.assertEqual(self.shots("s.subject_n, s.shot_size")[2], (1, "wide"))

    def test_shot_size_follows_how_much_of_the_frame_a_person_fills(self):
        self.assertEqual([analyze.shot_size(a) for a in (0.0, 0.01, 0.02, 0.1, 0.15, 0.6)],
                         ["wide", "wide", "medium", "medium", "close", "close"])


if __name__ == "__main__":
    unittest.main()
