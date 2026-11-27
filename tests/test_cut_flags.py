"""The flags that fall inside a cut, and what cutting them would save."""

import os
from unittest import mock

from constructive_media.edit import EditError, flags, timelines
from constructive_media.edit.timelines import Clip

from . import test_cli
from .support import TEST_DATABASE_VARIABLE, DatabaseTest
from .test_render import over

NAME = "made-up-lighthouse-cut"


class CutFlagsTest(DatabaseTest):
    """A camera and a collar microphone of one take, and a second camera on its own.

    The collar microphone was started five seconds after the camera.
    """

    def setUp(self):
        super().setUp()
        environment = mock.patch.dict(os.environ, {
            "CONSTRUCTIVE_MEDIA_DATABASE_URL": os.environ[TEST_DATABASE_VARIABLE]})
        environment.start()
        self.addCleanup(environment.stop)
        self.camera = self.asset("lamp room.mov", "video")
        self.collar = self.asset("collar.wav", "audio")
        self.harbour = self.asset("harbour.mov", "video")
        ((take,),) = self.query(
            "INSERT INTO media.takes (name, project) VALUES ('made-up-lighthouse', 'made-up') RETURNING id")
        self.query("""INSERT INTO media.take_assets (take_id, asset_id, role, is_primary, offset_s, offset_method)
                      VALUES (%s, %s, 'camera', true, 0, NULL), (%s, %s, 'lav', false, 5.0, 'words')
                      RETURNING asset_id""", (take, self.camera, take, self.collar))
        timelines.save(NAME, [
            # Heard from the camera, ten to fourteen seconds in.
            Clip(self.camera, 10.0, 14.0),
            # Seen through the camera and heard from the collar: the same three seconds on both.
            over(self.camera, 20.0, 23.0, self.collar, 15.0),
            Clip(self.harbour, 0.0, 2.0),
        ], project="made-up")

    def asset(self, name, medium):
        ((asset_id,),) = self.query(
            """INSERT INTO media.assets (medium, path, content_hash, has_audio, project, duration_s)
               VALUES (%s, %s, %s, true, 'made-up', 60) RETURNING id""",
            (medium, f"/made-up/{name}", f"made-up-{name}"))
        return asset_id

    def flag(self, asset_id, kind, start, end, *, action="cut", detail=None, accepted=None):
        self.query(
            """INSERT INTO media_video.flags (id, subject_type, subject_id, asset_id, kind, start_s, end_s,
                                              action, severity, detail, producer, accepted)
               VALUES (gen_random_uuid(), 'asset', %s, %s, %s, %s, %s, %s, 0.5, %s, 'made-up', %s) RETURNING id""",
            (asset_id, asset_id, kind, start, end, action, detail, accepted))

    def talk(self):
        self.flag(self.camera, "filler", 11.0, 11.4, detail="um")
        # The same um as the collar wrote it down, a tenth of a second later.
        self.flag(self.collar, "filler", 6.1, 6.5, detail="um")
        self.flag(self.camera, "crosstalk", 12.0, 13.0, action="review")
        self.flag(self.collar, "long_pause", 16.0, 17.0)
        # Written down from the camera, and half of it past the end of the clip.
        self.flag(self.camera, "repetition", 22.5, 24.0, detail="said twice")
        # Somebody said this one is not a filler, and this one is in no clip.
        self.flag(self.camera, "filler", 13.2, 13.9, accepted=False)
        self.flag(self.camera, "filler", 30.0, 31.0)

    def test_flags_are_placed_where_they_would_be_met_on_watching(self):
        self.talk()
        found = flags.in_cut(NAME)
        self.assertEqual(
            [(one.clip, round(one.at_s, 2), round(one.offset_s, 2), round(one.seconds, 2), one.kind, one.action,
              one.detail) for one in found],
            [(1, 1.0, 1.0, 0.4, "filler", "cut", "um"),
             (1, 1.1, 1.1, 0.4, "filler", "cut", "um"),
             (1, 2.0, 2.0, 1.0, "crosstalk", "review", None),
             (2, 5.0, 1.0, 1.0, "long_pause", "cut", None),
             (2, 6.5, 2.5, 0.5, "repetition", "cut", "said twice")])

    def test_what_cutting_would_save_counts_each_moment_once(self):
        self.talk()
        kinds, whole = flags.saving(flags.in_cut(NAME))
        # Two microphones wrote the one um down: it is one place, from where the first begins to where the last ends.
        self.assertEqual([(one.kind, one.places, round(one.seconds, 2)) for one in kinds],
                         [("long_pause", 1, 1.0), ("filler", 1, 0.5), ("repetition", 1, 0.5)])
        self.assertAlmostEqual(whole, 2.0)
        # A filler inside a stretch said twice is not taken out twice.
        self.flag(self.collar, "filler", 17.6, 17.9)
        kinds, whole = flags.saving(flags.in_cut(NAME))
        self.assertEqual([(one.kind, one.places, round(one.seconds, 2)) for one in kinds],
                         [("long_pause", 1, 1.0), ("filler", 2, 0.8), ("repetition", 1, 0.5)])
        self.assertAlmostEqual(whole, 2.0)
        self.assertEqual(flags.saving([]), ([], 0))

    def test_a_recording_set_beside_a_take_by_nothing_carries_no_flags_over(self):
        # The collar is in the take but nobody has worked out where it sits.
        self.query("UPDATE media.take_assets SET offset_method = NULL WHERE asset_id = %s RETURNING 1", (self.collar,))
        self.talk()
        self.assertEqual([(one.clip, one.kind, round(one.offset_s, 2)) for one in flags.in_cut(NAME)],
                         [(1, "filler", 1.0), (1, "crosstalk", 2.0), (2, "long_pause", 1.0)])

    def test_the_command_lists_them_and_says_what_would_go(self):
        self.talk()
        status, out, err = test_cli.run("flags", NAME)
        self.assertEqual((status, err), (0, ""))
        self.assertEqual([" ".join(line.split()) for line in out.splitlines()], [
            f"{NAME}: 5 flags in 2 of its 3 clips",
            "0:01.0 clip 1 +1.0 filler cut 0.4s um",
            "0:01.1 clip 1 +1.1 filler cut 0.4s um",
            "0:02.0 clip 1 +2.0 crosstalk review 1.0s",
            "0:05.0 clip 2 +1.0 long pause cut 1.0s",
            "0:06.5 clip 2 +2.5 repetition cut 0.5s said twice",
            "cutting what is marked to cut would take 2.0s out of 0:09.0:",
            "1.0s long pause, in 1 place",
            "0.5s filler, in 1 place",
            "0.5s repetition, in 1 place",
        ])
        # Nothing was changed by looking.
        self.assertEqual(len(timelines.load(NAME).clips), 3)
        self.assertEqual(self.query("SELECT count(*) FROM media_video.flags"), [(7,)])

    def test_a_cut_with_nothing_flagged_says_so(self):
        self.flag(self.camera, "filler", 30.0, 31.0)
        self.assertEqual(test_cli.run("flags", NAME), (0, f"{NAME}: no flags in its 3 clips\n", ""))
        self.flag(self.camera, "crosstalk", 12.0, 13.0, action="review")
        status, out, _ = test_cli.run("flags", NAME)
        self.assertEqual((status, out.splitlines()[0], len(out.splitlines())),
                         (0, f"{NAME}: 1 flag in 1 of its 3 clips", 2))

    def test_what_someone_asked_not_to_be_used_is_not_let_by(self):
        self.flag(self.harbour, "off_record", 0.5, 5.0, action="avoid", detail="asked to stop")
        status, out, err = test_cli.run("flags", NAME)
        self.assertEqual((status, err),
                         (1, f"media: clip 3 of {NAME} holds something someone asked not to be used\n"))
        self.assertEqual([" ".join(line.split()) for line in out.splitlines()], [
            f"{NAME}: 1 flag in 1 of its 3 clips",
            "0:07.5 clip 3 +0.5 off record avoid 1.5s asked to stop",
        ])

    def test_a_cut_that_is_not_there_is_refused_by_name(self):
        with self.assertRaisesRegex(EditError, "no timeline is called 'made-up-nothing'"):
            flags.in_cut("made-up-nothing")
        status, out, err = test_cli.run("flags", "made-up-nothing")
        self.assertEqual((status, out), (1, ""))
        self.assertIn("media: no timeline is called 'made-up-nothing'", err)
