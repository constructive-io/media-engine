"""Takes end to end: recordings of one made-up talk, grouped, matched and placed."""

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from constructive_media import takes
from constructive_media.takes import Member, TakeError, check, clock, group, match, sync
from constructive_media.takes.align import Alignment
from constructive_media.takes.loudness import Heard
from constructive_media.takes.words import spoken

from .support import TEST_DATABASE_VARIABLE, DatabaseTest, heard, make_talk, write_transcript
from .test_cli import run

NOON = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
TALK = make_talk(1200)

#: Who rolled when, in seconds of the talk: (began, length).
ROLLED = {"camera": (100, 600), "desk": (0, 1200), "collar": (150, 300), "late": (750, 350)}
#: What must be added to each one's time to reach the camera's.
TRUE = {name: began - ROLLED["camera"][0] for name, (began, _) in ROLLED.items()}


def said_by(name, **options):
    began, length = ROLLED[name]
    return heard(TALK, began, length, seed=sum(map(ord, name)), **options)


def member(name, *, primary=False, method=None, offset=0.0, clock_error_s=None, medium=None, sound=True):
    began, length = ROLLED.get(name, (0, 300))
    recorded = None if clock_error_s is None else NOON + timedelta(seconds=began + clock_error_s)
    return Member(name, name, "camera" if name == "camera" else "lav", medium or ("video" if name == "camera" else "audio"),
                  f"/made-up/{name}", float(length), primary, offset, None, method, recorded, sound)


class SolveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.said = {name: said_by(name) for name in ROLLED}

    def solve(self, *recordings, **options):
        solved = sync.solve(list(recordings), self.said, **options)
        return {p.member.asset_id: p for p in solved.placements}, solved

    def test_every_recording_is_placed_against_the_primary_and_checked(self):
        placed, solved = self.solve(member("desk"), member("camera", primary=True), member("collar"))
        self.assertEqual([p.member.asset_id for p in solved.placements], ["desk", "camera", "collar"])
        self.assertEqual((placed["camera"].state, placed["camera"].offset_s), ("primary", 0.0))
        for name in ("desk", "collar"):
            self.assertAlmostEqual(placed[name].offset_s, TRUE[name], delta=0.05)
            self.assertEqual((placed[name].method, placed[name].state), ("phrases", "ok"))
            self.assertGreater(placed[name].confidence, 50)
        self.assertIsNone(solved.started_at)

    def test_a_recording_the_primary_never_heard_is_placed_through_one_that_heard_both(self):
        placed, _ = self.solve(member("camera", primary=True), member("desk"), member("late"))
        self.assertAlmostEqual(placed["late"].offset_s, TRUE["late"], delta=0.08)
        # Nothing to compare with the camera, so the desk speaks for it.
        self.assertEqual((placed["late"].method, placed["late"].state), ("phrases", "ok"))
        alone, _ = self.solve(member("camera", primary=True), member("late"))
        self.assertEqual((alone["late"].placed, alone["late"].state), (False, "unplaced"))

    def test_a_take_needs_a_primary(self):
        with self.assertRaises(TakeError):
            sync.solve([member("desk"), member("collar")], self.said)

    def test_an_offset_a_person_set_is_never_moved_and_is_still_checked(self):
        placed, _ = self.solve(member("camera", primary=True), member("desk", method="manual", offset=-100.0),
                               member("collar", method="manual", offset=41.0))
        self.assertEqual((placed["desk"].offset_s, placed["desk"].method, placed["desk"].state), (-100.0, "manual", "ok"))
        self.assertEqual((placed["collar"].offset_s, placed["collar"].method), (41.0, "manual"))
        self.assertEqual(placed["collar"].state, "mismatch")
        self.assertAlmostEqual(placed["collar"].verdict.residual_s, 9.0, delta=1.5)

    def test_what_a_person_set_is_built_on(self):
        # The desk is fixed a little off true. The late recording is reached
        # through it and inherits where the person put it.
        placed, _ = self.solve(member("camera", primary=True), member("desk", method="manual", offset=-99.0),
                               member("late"))
        self.assertAlmostEqual(placed["late"].offset_s, TRUE["late"] + 1.0, delta=0.08)

    def test_sound_places_what_has_no_words_only_when_it_is_sure(self):
        quiet = member("quiet")
        asked = []

        def listen(ref, other, answer=Heard(-30.0, 31.26)):
            asked.append((ref.asset_id, other.asset_id))
            return answer

        placed, _ = self.solve(member("camera", primary=True), member("desk"), quiet, listen=listen)
        self.assertEqual(asked, [("camera", "quiet")])    # not asked about what words placed
        found = placed["quiet"]
        self.assertEqual((found.offset_s, found.method, found.confidence), (-30.0, "loudness", 31.3))
        self.assertEqual(found.state, check.NOTHING)
        for answer in (Heard(-30.0, 6.0), None):
            unsure, _ = self.solve(member("camera", primary=True), quiet, listen=lambda ref, other: answer)
            self.assertFalse(unsure["quiet"].placed)
        deaf, _ = self.solve(member("camera", primary=True), member("quiet", sound=False), listen=listen)
        self.assertFalse(deaf["quiet"].placed)

    def test_sound_that_the_words_contradict_is_not_believed(self):
        # Too little said to align by, enough to see that -40 is wrong.
        said = dict(self.said, collar=said_by("collar", miss=0.75)[:18])
        wrong = sync.solve([member("camera", primary=True), member("collar")], said,
                           listen=lambda ref, other: Heard(TRUE["collar"] - 10.0, 40.0))
        self.assertFalse(wrong.placements[1].placed)
        right = sync.solve([member("camera", primary=True), member("collar")], said,
                           listen=lambda ref, other: Heard(TRUE["collar"], 40.0))
        self.assertEqual(right.placements[1].method, "loudness")

    def test_clocks_shown_to_agree_place_what_is_left(self):
        camera = member("camera", primary=True, clock_error_s=0)
        quiet = Member("quiet", "quiet", "lav", "audio", "/made-up/quiet", 300.0, False, 0.0, None, None,
                       NOON + timedelta(seconds=130), True)
        placed, solved = self.solve(camera, member("desk", clock_error_s=2), quiet)
        self.assertAlmostEqual((solved.started_at - NOON).total_seconds(), 101.0, delta=0.05)
        found = placed["quiet"]
        self.assertEqual((found.method, found.confidence), ("clock", 0.5))
        self.assertAlmostEqual(found.offset_s, 29.0, delta=0.05)
        self.assertEqual(found.state, check.NOTHING)

    def test_clocks_that_disagree_or_stand_alone_place_nothing(self):
        camera = member("camera", primary=True, clock_error_s=0)
        quiet = Member("quiet", "quiet", "lav", "audio", "/made-up/quiet", 300.0, False, 0.0, None, None,
                       NOON + timedelta(seconds=130), True)
        for others in ((member("desk", clock_error_s=90),), (member("desk"),), ()):
            placed, solved = self.solve(camera, quiet, *others)
            self.assertFalse(placed["quiet"].placed)
            self.assertIsNone(solved.started_at)

    def test_a_clock_that_says_the_primary_was_not_rolling_places_nothing(self):
        camera = member("camera", primary=True, clock_error_s=0)
        elsewhere = Member("quiet", "quiet", "lav", "audio", "/made-up/quiet", 300.0, False, 0.0, None, None,
                           NOON + timedelta(hours=3), True)
        placed, _ = self.solve(camera, member("desk", clock_error_s=0), elsewhere)
        self.assertFalse(placed["quiet"].placed)

    def test_an_earlier_placing_is_kept_until_the_check_says_it_is_wrong(self):
        camera = member("camera", primary=True)
        kept, _ = self.solve(camera, member("quiet", method="loudness", offset=-30.0))
        self.assertEqual((kept["quiet"].offset_s, kept["quiet"].method), (-30.0, "loudness"))
        said = dict(self.said, collar=said_by("collar", miss=0.75)[:18])
        for offset, method in ((TRUE["collar"], "loudness"), (TRUE["collar"] + 10.0, None)):
            solved = sync.solve([camera, member("collar", method="loudness", offset=offset)], said)
            self.assertEqual(solved.placements[1].method, method, offset)

    def test_evidence_that_fails_the_check_is_passed_over_for_the_next_strongest(self):
        links = {"p": {"a": Alignment(10.0, 50, "phrases"), "b": Alignment(5.0, 9, "words")},
                 "a": {"b": Alignment(7.0, 40, "phrases")}}
        verdict = check.Verdict(check.OK)
        asked = []

        def prove(near, far, link, offset, refuse=()):
            asked.append((near, far, offset))
            return None if (near, far) in refuse else verdict

        reached = sync._reach({"p": 0.0}, links, prove)
        self.assertEqual(reached, {"a": (10.0, 50, "phrases", verdict), "b": (17.0, 40, "phrases", verdict)})
        self.assertEqual(asked, [("p", "a", 10.0), ("a", "b", 17.0)])
        reached = sync._reach({"p": 0.0}, links, lambda *link: prove(*link, refuse={("a", "b")}))
        self.assertEqual(reached["b"], (5.0, 9, "words", verdict))
        reached = sync._reach({"p": 0.0}, links, lambda *link: prove(*link, refuse={("a", "b"), ("p", "b")}))
        self.assertEqual(list(reached), ["a"])

    def test_checking_changes_nothing_and_judges_only_what_is_placed(self):
        checked = sync.verify([member("camera", primary=True), member("desk", method="words", offset=-100.0),
                               member("collar", method="words", offset=20.0), member("late")], self.said)
        self.assertEqual([p.state for p in checked], ["primary", "ok", "mismatch", "unplaced"])
        self.assertEqual([p.offset_s for p in checked], [0.0, -100.0, 20.0, 0.0])
        self.assertIsNone(checked[0].verdict)


class TakesTest(DatabaseTest):
    def asset(self, name, *, medium="audio", seconds=600.0, recorded_at=None, project="made-up",
              has_audio=True, said=None):
        ((asset_id,),) = self.query(
            """INSERT INTO media.assets (medium, path, content_hash, has_audio, project, duration_s, recorded_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (medium, f"/made-up/{name}", f"made-up-{name}", has_audio, project, seconds, recorded_at),
        )
        if said:
            write_transcript(asset_id, said)
        return asset_id

    def recording(self, name, file=None, *, clock_error_s=0.0, transcribed=True, timed=True, **options):
        began, length = ROLLED[name]
        return self.asset(
            file or f"{name}.{'mov' if name == 'camera' else 'wav'}",
            medium="video" if name == "camera" else "audio", seconds=float(length),
            recorded_at=NOON + timedelta(seconds=began + clock_error_s) if timed else None,
            said=said_by(name) if transcribed else None, **options)

    def members_of(self, name):
        return self.query(
            """SELECT ta.label, ta.role, ta.is_primary, ta.offset_s, ta.offset_method
                 FROM media.take_assets ta JOIN media.takes t ON t.id = ta.take_id
                WHERE t.name = %s ORDER BY ta.is_primary DESC, ta.label""", (name,))


class WordsAndTimesTest(TakesTest):
    def test_transcripts_are_read_together_as_words_to_compare(self):
        one = self.asset("one.wav", said=[("Harbour,", 0.5), ("—", 0.9), ("FOG.", 1.2)])
        two = self.asset("two.wav", said=[("bell", 3.0)])
        silent = self.asset("silent.wav")
        self.assertEqual(spoken([one, two, silent]),
                         {one: [("harbour", 0.5), ("fog", 1.2)], two: [("bell", 3.0)]})
        self.assertEqual(spoken([]), {})

    def test_a_recording_time_is_read_once_from_the_name_or_the_file(self):
        named = self.asset("REC_20260102_120000.wav")
        inside = self.asset("clip-7.mov", medium="video")
        neither = self.asset("untitled.wav")
        self.asset("frame.png", medium="image")
        self.asset("other_20260102_120000.wav", project="made-up-other")
        written = datetime(2026, 1, 2, 12, 0, 30, tzinfo=timezone.utc)
        opened = []

        def created(path):
            opened.append(str(path))
            return written if "clip-7" in str(path) else None

        with mock.patch("constructive_media.ffmpeg.created", created):
            first = clock.stamp("made-up", utc_offset_hours=-5)
            again = clock.stamp("made-up", utc_offset_hours=-5)
        self.assertEqual(first, {"filename": 1, "container": 1, "unknown": 1})
        self.assertEqual(again, {"filename": 0, "container": 0, "unknown": 0})
        self.assertEqual(opened, ["/made-up/clip-7.mov", "/made-up/untitled.wav"])
        times = dict(self.query(
            "SELECT id, recorded_at FROM media.assets WHERE id = ANY(%s::uuid[])", ([str(named), str(inside), str(neither)],)))
        self.assertEqual(times[named], datetime(2026, 1, 2, 17, 0, tzinfo=timezone.utc))
        self.assertEqual((times[inside], times[neither]), (written, None))
        sources = self.query("SELECT meta->>'time_from' FROM media.assets WHERE project = 'made-up' ORDER BY path")
        self.assertEqual(sources, [("filename",), ("container",), (None,), ("unknown",)])


class GroupTest(TakesTest):
    def test_recordings_that_rolled_together_become_one_take_with_picture_first(self):
        desk, camera, collar = self.recording("desk"), self.recording("camera"), self.recording("collar")
        self.asset("another-day.wav", recorded_at=NOON + timedelta(days=1))
        self.asset("no-clock.wav")
        (proposal,) = group.by_clock("made-up", prefix="talk")
        self.assertEqual((proposal.action, proposal.take), ("new", "talk-01"))
        self.assertEqual([r.asset_id for r in proposal.recordings], [camera, desk, collar])
        self.assertEqual(self.query("SELECT count(*) FROM media.takes"), self.query("SELECT 0::bigint"))

        self.assertEqual(group.apply([proposal]), 3)
        self.assertEqual(self.members_of("talk-01"), [
            ("camera", "camera", True, 0.0, None), ("collar", "lav", False, 0.0, None),
            ("desk", "lav", False, 0.0, None)])
        self.assertEqual(self.query("SELECT project FROM media.takes WHERE name = 'talk-01'"), [("made-up",)])
        self.assertEqual(group.by_clock("made-up"), [])    # nothing left to do

    def test_takes_are_numbered_past_the_ones_there_are(self):
        takes.create("talk-01", None)
        self.recording("camera")
        self.recording("desk")
        self.asset("b-cam.mov", medium="video", recorded_at=NOON + timedelta(days=1))
        self.asset("b-mic.wav", recorded_at=NOON + timedelta(days=1, seconds=30))
        self.assertEqual(sorted(p.take for p in group.by_clock(prefix="talk")), ["talk-02", "talk-03"])

    def test_a_late_arrival_joins_the_take_its_group_has(self):
        self.recording("camera")
        self.recording("desk")
        group.apply(group.by_clock())
        late = self.recording("late")
        again = self.recording("desk", "copy/desk.wav")
        (proposal,) = group.by_clock()
        self.assertEqual((proposal.action, proposal.take), ("join", "take-01"))
        self.assertEqual({r.asset_id for r in proposal.recordings}, {late, again})
        self.assertEqual(group.apply([proposal]), 2)
        labels = [label for label, *_ in self.members_of("take-01")]
        self.assertEqual(labels[:2] + labels[3:], ["camera", "desk", "late"])
        self.assertRegex(labels[2], r"^desk-[0-9a-f]{6}$")    # two files called desk

    def test_a_group_that_spans_two_takes_is_left_for_a_person(self):
        first, second = takes.create("morning", None), takes.create("afternoon", None)
        takes.add(first, self.recording("camera"), role="camera", label="camera", is_primary=True)
        takes.add(second, self.recording("late"), role="lav", label="late", is_primary=True)
        desk = self.recording("desk")
        (proposal,) = group.by_clock()
        self.assertEqual((proposal.action, proposal.take), ("skip", "afternoon, morning"))
        self.assertEqual([r.asset_id for r in proposal.recordings], [desk])
        self.assertEqual(group.apply([proposal]), 0)

    def test_what_the_engine_made_from_a_recording_is_not_a_recording(self):
        self.recording("camera")
        cut = self.recording("desk", "desk-cut.wav")
        self.query("INSERT INTO media.derivations (output_id, kind, tool) VALUES (%s, 'snippet', 'made-up') RETURNING id", (cut,))
        self.assertEqual(group.by_clock(), [])
        self.assertEqual(group.alone(), [group.Proposal("new", "camera", group.alone()[0].recordings)])

    def test_overlap_shorter_than_asked_is_not_a_take(self):
        self.recording("desk")
        self.asset("tail.wav", recorded_at=NOON + timedelta(seconds=1170))
        self.assertEqual(group.by_clock(), [])
        self.assertEqual(len(group.by_clock(min_overlap_s=20)), 1)

    def test_a_transcribed_recording_on_its_own_can_have_a_take_of_its_own(self):
        takes.create("desk", None)
        desk = self.recording("desk", timed=False)
        self.recording("collar", timed=False, transcribed=False)
        self.asset("blip.wav", seconds=2.0, said=[("bell", 0.5)])
        (proposal,) = group.alone("made-up")
        self.assertEqual(proposal.take, f"desk-{str(desk)[:6]}")
        self.assertEqual(group.apply([proposal]), 1)
        self.assertEqual(self.members_of(proposal.take), [("desk", "lav", True, 0.0, None)])
        self.assertEqual(group.alone("made-up"), [])


class SyncTest(TakesTest):
    def take(self, *names, **options):
        take_id = takes.create("talk", "made-up")
        for name in names:
            takes.add(take_id, self.recording(name, **options), role="camera" if name == "camera" else "lav",
                      label=name, is_primary=name == "camera")
        return takes.find("talk")

    def test_a_take_is_placed_and_the_placing_stored_with_how_it_was_reached(self):
        take = self.take("camera", "desk", "collar", "late")
        dry = sync.sync(take, by_sound=False, write=False)
        self.assertEqual([p.state for p in dry.placements], ["primary", "ok", "ok", "ok"])
        self.assertEqual({method for *_, method in self.members_of("talk")}, {None})

        solved = sync.sync(take, by_sound=False)
        stored = {label: (offset, method) for label, _, _, offset, method in self.members_of("talk")}
        self.assertEqual(stored["camera"], (0.0, None))
        for name in ("desk", "collar", "late"):
            self.assertAlmostEqual(stored[name][0], TRUE[name], delta=0.08)
            self.assertEqual(stored[name][1], "phrases")
        (((started_at,),)) = self.query("SELECT started_at FROM media.takes WHERE name = 'talk'")
        self.assertEqual(started_at, solved.started_at)
        self.assertAlmostEqual((started_at - NOON).total_seconds(), 100.0, delta=0.05)
        (listed,) = takes.listing("made-up")
        self.assertEqual((listed.name, listed.members, listed.placed), ("talk", 4, 4))
        self.assertEqual(takes.listing("made-up-other"), [])
        # Run again, nothing changes.
        self.assertEqual(sync.store(take, sync.solve(takes.members(take.id), spoken(m.asset_id for m in takes.members(take.id)))), 0)

    def test_a_person_s_offset_and_the_primary_are_never_written_over(self):
        take = self.take("camera", "desk", "collar")
        self.query("""UPDATE media.take_assets SET offset_s = -99.0, offset_method = 'manual'
                       WHERE label = 'desk' AND take_id = %s RETURNING 1""", (take.id,))
        sync.sync(take, by_sound=False)
        stored = {label: (offset, method) for label, _, _, offset, method in self.members_of("talk")}
        self.assertEqual((stored["desk"], stored["camera"]), ((-99.0, "manual"), (0.0, None)))
        self.assertEqual(stored["collar"][1], "phrases")

    def test_stored_offsets_are_checked_without_being_changed(self):
        take = self.take("camera", "desk", "collar")
        sync.sync(take, by_sound=False)
        self.query("UPDATE media.take_assets SET offset_s = offset_s + 12 WHERE label = 'collar' AND take_id = %s RETURNING 1",
                   (take.id,))
        states = {p.member.label: p.state for p in sync.recheck(take)}
        self.assertEqual(states, {"camera": "primary", "desk": "ok", "collar": "mismatch"})
        self.assertAlmostEqual(self.members_of("talk")[1][3], TRUE["collar"] + 12, delta=0.08)

    def test_takes_are_chosen_by_name_or_all_that_have_company(self):
        self.take("camera", "desk")
        takes.add(takes.create("solo", "made-up"), self.asset("solo.wav"), role="lav", label="solo", is_primary=True)
        self.assertEqual([t.name for t in sync.chosen(project="made-up")], ["talk"])
        self.assertEqual([t.name for t in sync.chosen(["solo", "talk"])], ["solo", "talk"])
        with self.assertRaisesRegex(TakeError, "no take is called 'nowhere'"):
            sync.chosen(["nowhere"])
        with self.assertRaisesRegex(TakeError, "already exists"):
            takes.create("talk", None)

    def test_the_listener_reads_each_file_once_and_only_files_that_exist(self):
        read = []

        def rises(source):
            read.append(source)
            return "shape of " + source

        with mock.patch("constructive_media.takes.loudness.rises", rises), \
             mock.patch("constructive_media.takes.loudness.offset", lambda ref, other: (ref, other)), \
             mock.patch("constructive_media.takes.sync.can_listen", lambda: True):
            listen = sync.listener()
            here = Member("a", "a", "lav", "audio", __file__, 1.0, True, 0.0, None, None, None, True)
            gone = Member("b", "b", "lav", "audio", "/made-up/gone.wav", 1.0, False, 0.0, None, None, None, True)
            self.assertEqual(listen(here, gone), ("shape of " + __file__, None))
            self.assertEqual(listen(here, here), ("shape of " + __file__,) * 2)
        self.assertEqual(read, [__file__])
        with mock.patch("constructive_media.takes.sync.can_listen", lambda: False):
            self.assertIsNone(sync.listener())


class MatchTest(TakesTest):
    def test_a_recorder_with_no_clock_finds_its_camera_by_what_is_said(self):
        camera = self.recording("camera", timed=False)
        self.asset("b-cam.mov", medium="video", said=heard(make_talk(600, seed=9), 0, 600))
        desk = self.recording("desk", timed=False)
        stranger = self.asset("stranger.wav", said=heard(make_talk(600, seed=10), 0, 600))
        self.asset("quiet.wav")
        self.asset("short.wav", seconds=10.0, said=said_by("collar")[:12])
        found = {m.recording: m for m in match.find("made-up")}
        self.assertEqual(set(found), {desk, stranger})
        self.assertEqual((found[stranger].camera, found[stranger].confirmed), (None, False))
        self.assertEqual((found[desk].camera, found[desk].confirmed), (camera, True))
        self.assertAlmostEqual(found[desk].alignment.offset_s, TRUE["desk"], delta=0.05)
        self.assertEqual(found[desk].alignment.method, "phrases")

        added, left = match.apply(list(found.values()), prefix="talk")
        self.assertEqual((added, left), (1, []))
        (camera_row, desk_row) = self.members_of("talk-01")
        self.assertEqual(camera_row, ("camera", "camera", True, 0.0, None))
        self.assertEqual(desk_row[:3] + desk_row[4:], ("desk", "lav", False, "phrases"))
        self.assertAlmostEqual(desk_row[3], TRUE["desk"], delta=0.05)
        self.assertEqual(match.find("made-up")[0].recording, stranger)    # the desk is home

    def test_a_match_joins_the_take_its_camera_is_in_on_that_takes_time(self):
        # The desk is the primary here, and the camera sits 100 s into it.
        take = takes.create("talk", "made-up")
        takes.add(take, self.recording("desk", timed=False), role="lav", label="desk", is_primary=True)
        camera = self.recording("camera", timed=False)
        takes.add(take, camera, role="camera", label="camera", offset_s=100.0, method="manual")
        self.recording("collar", timed=False)
        self.assertEqual(match.apply(match.find())[0], 1)
        stored = {label: offset for label, _, _, offset, _ in self.members_of("talk")}
        self.assertAlmostEqual(stored["collar"], 150.0, delta=0.05)

    def test_a_camera_whose_own_place_is_unknown_cannot_place_another(self):
        take = takes.create("talk", "made-up")
        takes.add(take, self.recording("desk", timed=False, transcribed=False), role="lav", label="desk", is_primary=True)
        takes.add(take, self.recording("camera", timed=False), role="camera", label="camera")
        self.recording("collar", timed=False)
        found = match.find()
        added, ((left, reason),) = match.apply(found)
        self.assertEqual((added, left.recording_path), (0, "/made-up/collar.wav"))
        self.assertIn("media sync", reason)


class CommandsTest(TakesTest):
    def setUp(self):
        super().setUp()
        environment = mock.patch.dict(os.environ, {
            "CONSTRUCTIVE_MEDIA_HOME": "/made-up/home",
            "CONSTRUCTIVE_MEDIA_DATABASE_URL": os.environ[TEST_DATABASE_VARIABLE],
        })
        environment.start()
        self.addCleanup(environment.stop)
        # The recorders wrote the time in the file name and nowhere else.
        for name, file in (("camera", "CAM_20260102_120140.mov"), ("desk", "DESK_20260102_120000.wav"),
                           ("collar", "COLLAR_20260102_120230.wav")):
            self.recording(name, file, timed=False)

    def test_from_loose_files_to_a_checked_take(self):
        proposed = (
            "recording times read: 3 from file names, 0 from inside the file, 0 not found\n"
            "talk-01  (new)\n"
            "  primary  CAM_20260102_120140.mov\n"
            "  lav      DESK_20260102_120000.wav\n"
            "  lav      COLLAR_20260102_120230.wav\n"
            "3 recordings placed in 1 takes"
        )
        self.assertEqual(run("group", "--prefix", "talk", "--dry-run")[:2], (0, proposed + "; nothing was written\n"))
        self.assertEqual(self.query("SELECT count(*) FROM media.takes"), [(0,)])
        self.assertEqual(self.query("SELECT count(*) FROM media.assets WHERE recorded_at IS NOT NULL"), [(0,)])
        self.assertEqual(run("group", "--prefix", "talk", "--project", "made-up")[:2], (0, proposed + "\n"))
        self.assertEqual(run("group", "--prefix", "talk")[1], "0 recordings placed in 0 takes\n")
        self.assertEqual(run("takes")[1], "    1 of 3   placed  talk-01  (made-up)\n1 takes\n")

        status, out, _ = run("sync", "--no-sound", "--dry-run")
        self.assertEqual(status, 0)
        self.assertTrue(out.endswith("1 takes: 0 wrong, 0 not placed; nothing was written\n"), out)
        self.assertEqual(run("takes", "--project", "made-up")[1], "    1 of 3   placed  talk-01  (made-up)\n1 takes\n")

        status, out, _ = run("sync", "talk-01", "--no-sound")
        self.assertEqual(status, 0)
        lines = out.splitlines()
        self.assertEqual(lines[0], "talk-01")
        self.assertRegex(lines[1], r"^  primary +camera  CAM_20260102_120140$")
        self.assertRegex(lines[2], r"^  ok +\+50\.0\ds  phrases +lav +COLLAR_20260102_120230$")
        self.assertRegex(lines[3], r"^  ok +-100\.0\ds  phrases +lav +DESK_20260102_120000$")
        self.assertEqual(lines[4:], ["1 takes: 0 wrong, 0 not placed"])
        self.assertEqual(run("takes")[1], "    3 of 3   placed  talk-01  (made-up)\n1 takes\n")

        status, out, _ = run("takes", "talk-01")
        self.assertEqual((status, out.splitlines()[0]), (0, "talk-01  (made-up)"))
        self.assertRegex(out, r"\n  placed +-100\.0\ds  phrases +lav +DESK_20260102_120000\n")
        self.assertEqual(run("sync", "--check")[0], 0)
        self.assertRegex(run("status")[1], r"takes +1\b")

    def test_a_wrong_offset_fails_the_check_and_the_command(self):
        run("group")
        run("sync", "--no-sound")
        self.query("UPDATE media.take_assets SET offset_s = offset_s + 12 WHERE label LIKE 'COLLAR%%' RETURNING 1")
        status, out, _ = run("sync", "--check")
        self.assertEqual(status, 1)
        self.assertRegex(out, r"\n  mismatch \(about 1[0-4]s out\) +\+62\.0\ds  phrases +lav +COLLAR_20260102_120230\n")
        self.assertTrue(out.endswith("1 takes: 1 wrong, 0 not placed\n"), out)
        # Syncing again puts it right.
        self.assertEqual(run("sync", "--no-sound")[0], 0)
        self.assertEqual(run("sync", "--check", "take-01")[0], 0)

    def test_a_recorder_with_no_clock_is_matched_by_what_it_heard(self):
        run("group")
        began, length = ROLLED["late"]
        self.asset("pocket.wav", seconds=200.0, said=heard(TALK, 300, 200, seed=77))
        self.asset("stranger.wav", said=heard(make_talk(600, seed=10), 0, 600))
        status, out, _ = run("match", "--dry-run")
        self.assertEqual(status, 0)
        self.assertRegex(out, r"^  matched +pocket\.wav  with  CAM_20260102_120140\.mov  \+200\.0\ds  \d+ shared words\n"
                              r"  no camera +stranger\.wav\n1 of 2 recordings matched; nothing was written\n$")
        self.assertEqual(self.query("SELECT count(*) FROM media.take_assets"), [(3,)])
        self.assertTrue(run("match")[1].endswith("1 of 2 recordings added to their camera's take\n"))
        self.assertEqual(self.query("SELECT count(*) FROM media.take_assets"), [(4,)])

        status, out, _ = run("group", "--singles")
        self.assertEqual((status, out), (0, "stranger  (new)\n  primary  stranger.wav\n1 recordings placed in 1 takes\n"))
        self.assertEqual(run("sync", "--no-sound")[0], 0)

    def test_a_take_that_is_not_there_is_one_line(self):
        for command in (("takes", "nowhere"), ("sync", "nowhere")):
            status, out, err = run(*command)
            self.assertEqual((status, out, err), (1, "", "media: no take is called 'nowhere'; `media takes` lists them\n"))

    def test_without_numpy_sync_says_so_and_carries_on(self):
        run("group")
        with mock.patch("constructive_media.takes.sync.can_listen", lambda: False):
            status, out, _ = run("sync")
        self.assertEqual(status, 0)
        self.assertTrue(out.startswith("comparing by sound needs numpy"), out)
        self.assertTrue(out.endswith("1 takes: 0 wrong, 0 not placed\n"), out)


if __name__ == "__main__":
    unittest.main()
