"""Takes made and mended by hand, the sentences two recordings share, and one moment cut from each."""

import argparse
import tempfile
import unittest
import wave
from pathlib import Path

from constructive_media import derive, ffmpeg, takes
from constructive_media.cli.takes import _moment, _spread
from constructive_media.edit import passages
from constructive_media.takes import TakeError, hand, overlap, pull, sync

from .support import DatabaseTest, make_tone, make_video, needs_ffmpeg, settings_in, write_lines
from .test_cli import run
from .test_edit import TALK, Interview

#: What a made-up recorder on a quay wrote down: when each line starts, and the line.
QUAY = (
    (10.0, "The ferry leaves the island at seven every morning."),
    (16.0, "Nobody on the quay had seen the grey heron before."),
    (22.0, "We painted the boathouse door green, twice, that summer."),
    (30.0, "The ferry leaves the island at seven every morning."),
    (36.0, "Her brother kept bees behind the chapel wall."),
    (41.0, "Yes."),
    (44.0, "Thank you all for coming."),
)
#: The same morning from a second recorder, started four seconds later and hearing a little differently.
BOAT = (
    (12.1, "Nobody on the key had seen the gray heron before"),
    (17.9, "We painted the boat house door green twice that summer"),
    (26.0, "The ferry leaves the island at seven every morning"),
    (32.05, "Her brother kept bees behind the chapel wall"),
    (37.0, "Yes"),
    (50.0, "Somebody else entirely was talking about the weather forecast"),
)


def lines(spoken):
    return [one for one in (overlap.sentence(start, text) for start, text in spoken)
            if len(one.key) >= overlap.FEWEST_WORDS]


class SharedSentencesTest(unittest.TestCase):
    def test_a_line_is_known_by_its_telling_words(self):
        self.assertEqual(overlap.key("We painted the BOATHOUSE door green, twice, that summer."),
                         ("painted", "boathouse", "door", "green", "twice", "summer"))
        self.assertEqual(overlap.key("Yes, it is so."), ())
        self.assertEqual([one.start_s for one in lines(QUAY)], [10.0, 16.0, 22.0, 30.0, 36.0])

    def test_the_sentences_two_recordings_share_agree_on_how_far_apart_they_are(self):
        found = overlap.shared(lines(QUAY), lines(BOAT))
        self.assertAlmostEqual(found.offset_s, 3.975)
        self.assertEqual([(pair.ref.start_s, pair.other.start_s) for pair in found.pairs],
                         [(16.0, 12.1), (22.0, 17.9), (30.0, 26.0), (36.0, 32.05)])
        self.assertEqual((len(found.pairs), found.found, found.reason), (4, 4, ""))
        self.assertAlmostEqual(found.spread_s, 0.2)
        self.assertAlmostEqual(found.pairs[0].offset_s, 3.9)
        self.assertEqual(found.pairs[3].alike, 1.0)
        self.assertLess(found.pairs[0].alike, 1.0)

    def test_a_sentence_said_twice_is_placed_where_the_others_agree(self):
        pairs = overlap.candidates(lines(QUAY), lines(BOAT))
        ferry = sorted(pair.ref.start_s for pair in pairs if pair.other.start_s == 26.0)
        self.assertEqual(ferry, [10.0, 30.0])
        kept = overlap.agreeing(pairs).pairs
        self.assertEqual([pair.ref.start_s for pair in kept if pair.other.start_s == 26.0], [30.0])

    def test_a_sentence_alike_by_chance_is_counted_and_left_out(self):
        again = BOAT + ((70.0, "Nobody on the quay had seen the grey heron before"),)
        found = overlap.shared(lines(QUAY), lines(again))
        self.assertEqual((len(found.pairs), found.found), (4, 5))
        self.assertAlmostEqual(found.offset_s, 3.975)

    def test_too_little_in_common_is_shown_and_rests_no_offset(self):
        few = overlap.shared(lines(QUAY), lines(BOAT[:2]))
        self.assertIsNone(few.offset_s)
        self.assertEqual((len(few.pairs), few.reason), (2, "only 2 sentences agree, too few to rest an offset on"))
        one = overlap.shared(lines(QUAY), lines(BOAT[:1]))
        self.assertEqual((len(one.pairs), one.reason), (1, "only one sentence agrees, too few to rest an offset on"))
        none = overlap.shared(lines(QUAY), lines(BOAT[5:]))
        self.assertEqual((none.pairs, none.offset_s, none.reason), ((), None, "they have no sentence in common"))

    def test_how_alike_and_how_close_are_for_the_asker_to_say(self):
        self.assertEqual(len(overlap.shared(lines(QUAY), lines(BOAT), min_alike=0.99).pairs), 2)
        tight = overlap.shared(lines(QUAY), lines(BOAT), tolerance_s=0.06)
        self.assertEqual([pair.other.start_s for pair in tight.pairs], [26.0, 32.05])


class AskedForTest(unittest.TestCase):
    def test_a_recording_is_asked_for_with_what_it_is_and_what_to_call_it(self):
        self.assertEqual(hand.wanted("desk.wav"), hand.Wanted("desk.wav"))
        self.assertEqual(hand.wanted("desk.wav:board"), hand.Wanted("desk.wav", "board"))
        self.assertEqual(hand.wanted(" desk.wav :board: the desk "), hand.Wanted("desk.wav", "board", "the desk"))
        self.assertEqual(hand.wanted("desk.wav::desk"), hand.Wanted("desk.wav", None, "desk"))
        with self.assertRaisesRegex(TakeError, "'boom' is not something a recording can be to a take"):
            hand.wanted("desk.wav:boom")
        with self.assertRaisesRegex(TakeError, "names no recording"):
            hand.wanted(":lav")

    def test_a_time_is_seconds_or_what_a_clock_shows(self):
        self.assertEqual([_moment(text) for text in ("754.25", "12:34.25", "1:02:03", "-5", "+0:07", "-0:05.5")],
                         [754.25, 754.25, 3723.0, -5.0, 7.0, -5.5])
        for wrong in ("", "soon", "1:2:3:4", "1::2", "nan", "1:-2"):
            with self.assertRaises(argparse.ArgumentTypeError):
                _moment(wrong)

    def test_a_few_of_many_are_spread_from_first_to_last(self):
        self.assertEqual(_spread(list(range(10)), 4), [0, 3, 6, 9])
        self.assertEqual(_spread(list(range(3)), 12), [0, 1, 2])
        self.assertEqual((_spread(list(range(5)), 1), _spread(list(range(5)), 0)), ([0], []))


class ByHandTest(Interview):
    def rows(self, take="made-up-lighthouse"):
        return self.query(
            """SELECT substring(a.path from '[^/]+$'), ta.role, ta.label, ta.is_primary, ta.offset_s,
                      ta.offset_method, ta.offset_confidence
                 FROM media.take_assets ta JOIN media.takes t ON t.id = ta.take_id
                 JOIN media.assets a ON a.id = ta.asset_id
                WHERE t.name = %s ORDER BY ta.is_primary DESC, a.path""", (take,))

    def test_a_take_is_made_by_hand_from_recordings_named_as_a_person_would(self):
        self.asset("wide.mov", medium="video")
        self.asset("desk-left.wav")
        self.asset("pocket.wav")
        status, out, err = run("take", "made-up-evening", "--project", "made-up", "--primary", "wide.mov",
                               "--add", "desk-:board:desk", "--add", "/made-up/pocket.wav")
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(self.rows("made-up-evening"), [
            ("wide.mov", "camera", "wide", True, 0.0, None, None),
            ("desk-left.wav", "board", "desk", False, 0.0, None, None),
            ("pocket.wav", "lav", "pocket", False, 0.0, None, None),
        ])
        self.assertEqual(self.query("SELECT project FROM media.takes WHERE name = 'made-up-evening'"), [("made-up",)])
        self.assertEqual(out.splitlines()[:4], [
            "made take made-up-evening",
            "  primary      wide  as camera",
            "  added        desk  as board",
            "  added        pocket  as lav",
        ])
        self.assertIn("made-up-evening  (made-up)", out)
        self.assertRegex(out, r"primary +camera +wide")
        self.assertRegex(out, r"not placed +board +desk")

    def test_a_take_that_is_not_there_needs_a_primary_to_be_made(self):
        self.asset("desk.wav")
        status, out, err = run("take", "made-up-evening", "--add", "desk.wav")
        self.assertEqual((status, out), (1, ""))
        self.assertEqual(err, "media: no take is called 'made-up-evening'; to make it, say which recording is "
                              "its primary with --primary\n")
        self.assertEqual(self.query("SELECT 1 FROM media.takes WHERE name = 'made-up-evening'"), [])
        self.assertEqual(run("take", "made-up-evening")[0], 1)

    def test_a_take_is_shown_when_nothing_is_asked_of_it(self):
        status, out, _ = run("take", "made-up-lighthouse")
        self.assertEqual(status, 0)
        self.assertEqual(out, run("takes", "made-up-lighthouse")[1])
        self.assertRegex(out, r"placed +\+5\.00s +words +lav")

    def test_a_recording_is_found_by_path_file_name_or_a_pattern_that_fits_only_it(self):
        desk = self.asset("desk.wav")
        self.asset("desk-spare.wav")
        self.asset("poster.png", medium="image")
        self.assertEqual(hand.recording("/made-up/desk.wav").asset_id, desk)
        self.assertEqual(hand.recording("desk.wav").asset_id, desk)
        self.assertEqual(hand.recording("SPARE").name, "desk-spare.wav")
        self.assertEqual(hand.recording(r"lighthouse\.mov$").asset_id, self.camera)
        with self.assertRaisesRegex(TakeError, r"2 recordings in the library match 'desk' \(desk-spare.wav, desk.wav\)"):
            hand.recording("desk")
        with self.assertRaisesRegex(TakeError, "no recording in the library is called or matches 'poster'"):
            hand.recording("poster")
        with self.assertRaisesRegex(TakeError, "neither a recording's name nor a pattern"):
            hand.recording("desk(")

    def test_a_file_the_engine_cut_is_not_a_recording_to_put_in_a_take(self):
        cut = self.asset("made-up-lighthouse-12.00s-collar.wav")
        self.query("INSERT INTO media.derivations (output_id, kind, tool) VALUES (%s, 'snippet', 'media pull') "
                   "RETURNING id", (cut,))
        self.assertEqual(hand.recording("collar").asset_id, self.collar)

    def test_a_recording_of_a_take_is_named_by_its_label_first(self):
        self.query("UPDATE media.take_assets SET label = 'neck' WHERE asset_id = %s RETURNING 1", (self.collar,))
        spare = self.asset("neck-spare.wav")
        self.join(spare, "ambient", method=None)
        self.assertEqual(hand.member(self.take, "neck").asset_id, self.collar)
        self.assertEqual(hand.member(self.take, "collar").asset_id, self.collar)
        self.assertEqual(hand.member(self.take, "collar.wav").asset_id, self.collar)
        self.assertEqual(hand.member(self.take, "spare").asset_id, spare)
        self.assertEqual(hand.member(self.take, "light.*mov").asset_id, self.camera)
        with self.assertRaisesRegex(TakeError, "2 recordings in take 'made-up-lighthouse' match 'wav'"):
            hand.member(self.take, "wav")
        with self.assertRaisesRegex(TakeError, "no recording in take 'made-up-lighthouse' is called or matches 'desk'"):
            hand.member(self.take, "desk")

    def test_what_a_recording_is_to_its_take_is_changed_and_a_label_nobody_gave_stays_ungiven(self):
        status, out, _ = run("take", "made-up-lighthouse", "--add", "collar.wav:ambient")
        self.assertEqual(status, 0)
        self.assertIn("  changed      /made-up/collar.wav  as ambient", out)
        self.assertEqual(self.rows()[1], ("collar.wav", "ambient", None, False, 5.0, "words", None))
        status, out, _ = run("take", "made-up-lighthouse", "--add", "collar::neck", "--add", "lighthouse.mov")
        self.assertEqual(status, 0)
        self.assertIn("  changed      neck  as ambient", out)
        self.assertIn("  as it was    /made-up/lighthouse.mov  as camera", out)
        self.assertEqual(self.rows()[1][:3], ("collar.wav", "ambient", "neck"))
        self.asset("desk.wav")
        status, _, err = run("take", "made-up-lighthouse", "--add", "desk.wav:board:neck")
        self.assertEqual((status, err), (1, "media: another recording in take 'made-up-lighthouse' is already "
                                            "labelled 'neck'\n"))
        self.assertEqual(len(self.rows()), 2)

    def test_a_recording_put_in_a_second_take_is_said_to_be_in_the_first(self):
        self.asset("wide.mov", medium="video")
        status, out, _ = run("take", "made-up-evening", "--primary", "wide.mov", "--add", "collar.wav")
        self.assertEqual(status, 0)
        self.assertIn("  added        collar  as lav; it is also in made-up-lighthouse", out)

    def test_an_offset_set_by_hand_is_kept_as_manual(self):
        status, out, err = run("take", "made-up-lighthouse", "--offset", "collar", "-3.5")
        self.assertEqual((status, err), (0, ""))
        self.assertIn("  set by hand  /made-up/collar.wav  -3.50s", out)
        self.assertRegex(out, r"placed +-3\.50s +manual +lav")
        self.assertEqual(self.rows()[1][4:], (-3.5, "manual", None))
        self.assertEqual(run("take", "made-up-lighthouse", "--offset", "collar", "+12.25")[0], 0)
        self.assertEqual(self.rows()[1][4:], (12.25, "manual", None))

    def test_sync_moves_what_it_measured_and_never_what_a_person_set(self):
        self.query("UPDATE media.take_assets SET offset_s = 1.5, offset_method = 'clock' WHERE asset_id = %s "
                   "RETURNING 1", (self.collar,))
        sync.sync(self.take, by_sound=False)
        measured = self.rows()[1]
        self.assertAlmostEqual(measured[4], 5.0, delta=0.05)
        self.assertNotIn(measured[5], ("clock", "manual"))
        hand.place(self.take, "collar", 1.5)
        placed = {p.member.asset_id: p for p in sync.sync(self.take, by_sound=False).placements}
        self.assertEqual(self.rows()[1][4:], (1.5, "manual", None))
        self.assertEqual((placed[self.collar].offset_s, placed[self.collar].method), (1.5, "manual"))
        hand.forget(self.take, "collar")
        self.assertEqual(self.rows()[1][4:], (0.0, None, None))
        sync.sync(self.take, by_sound=False)
        self.assertAlmostEqual(self.rows()[1][4], 5.0, delta=0.05)

    def test_an_offset_is_forgotten_so_sync_places_the_recording_afresh(self):
        status, out, _ = run("take", "made-up-lighthouse", "--forget", "collar")
        self.assertEqual(status, 0)
        self.assertIn("  forgotten    the offset of /made-up/collar.wav; `media sync` places it afresh", out)
        self.assertRegex(out, r"not placed +lav")
        self.assertEqual(self.rows()[1][4:], (0.0, None, None))

    def test_the_primary_has_no_offset_to_set_or_forget_and_is_not_taken_out(self):
        for asked, said in ((("--offset", "lighthouse", "2"), "take time is its own time"),
                            (("--forget", "lighthouse"), "has no offset to forget"),
                            (("--remove", "lighthouse"), "make another recording the primary before taking it out"),
                            (("--offset", "collar", "soon"), "an offset is a number of seconds")):
            status, _, err = run("take", "made-up-lighthouse", *asked)
            self.assertEqual(status, 1)
            self.assertIn(said, err)
        self.assertEqual(self.rows()[0][3:6], (True, 0.0, None))

    def test_a_recording_is_taken_out_of_a_take_and_stays_in_the_library(self):
        status, out, _ = run("take", "made-up-lighthouse", "--remove", "collar")
        self.assertEqual(status, 0)
        self.assertIn("  taken out    /made-up/collar.wav", out)
        self.assertEqual([row[0] for row in self.rows()], ["lighthouse.mov"])
        self.assertEqual(self.query("SELECT count(*) FROM media.assets WHERE id = %s", (self.collar,)), [(1,)])

    def test_a_new_primary_carries_every_offset_over_to_its_own_time(self):
        desk = self.asset("desk.wav")
        self.join(desk, "board", offset=12.0, method="manual")
        loose = self.asset("loose.wav")
        self.join(loose, "ambient", method=None)
        self.query("UPDATE media.take_assets SET offset_confidence = 40 WHERE asset_id = %s RETURNING 1",
                   (self.collar,))
        status, out, err = run("take", "made-up-lighthouse", "--primary", "collar")
        self.assertEqual((status, err), (0, ""))
        self.assertIn("  primary      /made-up/collar.wav; every offset is now measured from it", out)
        self.assertEqual({row[0]: row[3:] for row in self.rows()}, {
            "collar.wav": (True, 0.0, None, None),
            "lighthouse.mov": (False, -5.0, "words", 40.0),
            "desk.wav": (False, 7.0, "manual", None),
            "loose.wav": (False, 0.0, None, None),
        })
        status, out, _ = run("take", "made-up-lighthouse", "--primary", "collar")
        self.assertEqual(status, 0)
        self.assertIn("  primary      /made-up/collar.wav, as it was", out)

    def test_a_recording_nothing_has_placed_is_not_made_primary_over_ones_that_are(self):
        self.asset("desk.wav")
        status, _, err = run("take", "made-up-lighthouse", "--primary", "desk.wav")
        self.assertEqual(status, 1)
        self.assertIn("desk has not been placed in take 'made-up-lighthouse'", err)
        self.assertEqual([row[0] for row in self.rows()], ["lighthouse.mov", "collar.wav"])
        # With nothing placed there is nothing to carry over, and nothing to lose.
        hand.forget(self.take, "collar")
        self.assertEqual(run("take", "made-up-lighthouse", "--primary", "desk.wav:board")[0], 0)
        self.assertEqual({row[0]: row[1:6] for row in self.rows()}, {
            "desk.wav": ("board", "desk", True, 0.0, None),
            "lighthouse.mov": ("camera", None, False, 0.0, None),
            "collar.wav": ("lav", None, False, 0.0, None),
        })

    def test_a_take_with_passages_keeps_its_primary(self):
        passages.propose(self.take)
        status, _, err = run("take", "made-up-lighthouse", "--primary", "collar")
        self.assertEqual(status, 1)
        self.assertIn("has 2 passages kept in the time of its primary", err)
        self.assertEqual(self.rows()[0][0], "lighthouse.mov")

    def test_a_take_that_lost_its_primary_is_given_one_and_its_offsets_still_agree(self):
        desk = self.asset("desk.wav")
        self.join(desk, "board", offset=12.0, method="manual")
        passages.propose(self.take)
        self.query("UPDATE media.take_assets SET is_primary = false, offset_method = 'clock' WHERE asset_id = %s "
                   "RETURNING 1", (self.camera,))
        # Its passages are still in the old primary's time, and would be left behind.
        with self.assertRaisesRegex(TakeError, "has 2 passages kept in the time of the primary it had"):
            hand.lead(self.take, "collar")
        self.query("DELETE FROM media_video.passages WHERE take_id = %s RETURNING 1", (self.take_id,))
        self.assertEqual(hand.lead(self.take, "collar")[1], True)
        self.assertEqual({row[0]: row[3:6] for row in self.rows()}, {
            "collar.wav": (True, 0.0, None),
            "lighthouse.mov": (False, -5.0, "clock"),
            "desk.wav": (False, 7.0, "manual"),
        })

    def test_a_dry_run_shows_the_take_as_it_would_be_and_writes_nothing(self):
        self.asset("desk.wav")
        status, out, _ = run("take", "made-up-lighthouse", "--add", "desk.wav:board", "--offset", "desk", "9",
                             "--remove", "collar", "--dry-run")
        self.assertEqual(status, 0)
        self.assertRegex(out, r"placed +\+9\.00s +manual +board +desk")
        self.assertNotRegex(out, r"lav")
        self.assertTrue(out.endswith("nothing was written\n"))
        self.assertEqual([row[0] for row in self.rows()], ["lighthouse.mov", "collar.wav"])

    def test_one_thing_refused_leaves_the_rest_undone(self):
        self.asset("desk.wav")
        status, _, err = run("take", "made-up-lighthouse", "--add", "desk.wav", "--offset", "nobody", "3")
        self.assertEqual(status, 1)
        self.assertIn("no recording in take 'made-up-lighthouse' is called or matches 'nobody'", err)
        self.assertEqual(len(self.rows()), 2)


class EvidenceTest(Interview):
    def test_each_recording_is_set_against_the_primary_beside_what_the_take_has_stored(self):
        (found,) = overlap.compare(self.take)
        self.assertEqual((found.ref.asset_id, found.other.asset_id, found.stored_s), (self.camera, self.collar, 5.0))
        self.assertAlmostEqual(found.shared.offset_s, 5.0)
        self.assertEqual((len(found.shared.pairs), found.shared.found), (len(TALK), len(TALK)))
        first = found.shared.pairs[0]
        self.assertEqual((first.ref.start_s, first.other.start_s, first.alike), (10.0, 5.0, 1.0))

    def test_the_evidence_is_shown_with_both_times_and_what_each_sentence_implies(self):
        status, out, err = run("overlap", "made-up-lighthouse", "--show", "3")
        self.assertEqual((status, err), (0, ""))
        shown = out.splitlines()
        self.assertEqual(shown[:4], [
            "made-up-lighthouse: what each recording says that /made-up/lighthouse.mov says too",
            "/made-up/collar.wav  (lav)",
            "  stored  +5.00s  words",
            "  shared  +5.00s  8 of the 8 sentences both say agree, to within 0.00s; 0.00s from the stored offset",
        ])
        self.assertEqual(shown[4].split(), ["lighthouse.m", "collar.wav", "implies", "alike"])
        self.assertEqual(shown[5].split()[:4], ["0:10.0", "0:05.0", "+5.00s", "1.00"])
        self.assertTrue(shown[5].endswith("So how long have you kept the light on the point?"))
        self.assertEqual([line.split()[0] for line in shown[5:8]], ["0:10.0", "0:26.0", "0:53.5"])
        self.assertEqual(shown[8], "  and 5 more between them")

    def test_a_recording_nothing_has_placed_is_told_what_to_store(self):
        desk = self.asset("desk.wav")
        self.query("INSERT INTO media.take_assets (take_id, asset_id, role, label) VALUES (%s, %s, 'board', 'the desk') "
                   "RETURNING 1", (self.take_id, desk))
        write_lines(desk, [(start + 31.25, text) for start, _, text in TALK[:5]])
        status, out, _ = run("overlap", "made-up-lighthouse", "the desk")
        self.assertEqual(status, 0)
        self.assertNotIn("collar", out)
        self.assertIn("  stored  not placed\n", out)
        self.assertIn("  shared  -31.25s  5 of the 5 sentences both say agree, to within 0.00s\n", out)
        self.assertIn("  to store it: media take made-up-lighthouse --offset 'the desk' -31.25\n", out)
        self.assertEqual(run("take", "made-up-lighthouse", "--offset", "the desk", "-31.25")[0], 0)
        (found,) = overlap.compare(self.take, only=hand.member(self.take, "the desk"))
        self.assertEqual((found.stored_s, found.other.method), (-31.25, "manual"))

    def test_any_recording_can_be_what_the_others_are_compared_with(self):
        desk = self.asset("desk.wav")
        self.join(desk, "board", offset=-31.25, method="manual")
        write_lines(desk, [(start + 31.25, text) for start, _, text in TALK])
        collar = hand.member(self.take, "collar")
        by_desk = {found.other.asset_id: found
                   for found in overlap.compare(self.take, against=hand.member(self.take, "desk"))}
        self.assertEqual(set(by_desk), {self.camera, self.collar})
        self.assertAlmostEqual(by_desk[self.collar].stored_s, 36.25)
        self.assertAlmostEqual(by_desk[self.collar].shared.offset_s, 36.25)
        self.assertAlmostEqual(by_desk[self.camera].stored_s, 31.25)
        with self.assertRaisesRegex(TakeError, "name a different recording"):
            overlap.compare(self.take, against=collar, only=collar)
        status, out, _ = run("overlap", "made-up-lighthouse", "collar", "--against", "desk")
        self.assertEqual(status, 0)
        self.assertIn("  stored  +36.25s  words", out)
        self.assertNotIn("to store it", out)

    def test_a_stored_offset_the_sentences_do_not_bear_out_is_plain_to_see(self):
        hand.place(self.take, "collar", -40.0)
        _, out, _ = run("overlap", "made-up-lighthouse")
        self.assertIn("  stored  -40.00s  manual", out)
        self.assertIn("agree, to within 0.00s; 45.00s from the stored offset", out)

    def test_with_no_transcript_or_nothing_in_common_it_says_so(self):
        quiet = self.asset("quiet.wav")
        self.join(quiet, "ambient", method=None)
        other = self.asset("other.wav")
        self.join(other, "ambient", method=None)
        write_lines(other, [(3.0, "Somebody else entirely was talking about the weather forecast."), (9.0, "Yes.")])
        found = {one.other.asset_id: one.shared for one in overlap.compare(self.take)}
        self.assertEqual((found[quiet].offset_s, found[quiet].reason), (None, "/made-up/quiet.wav has no transcript"))
        self.assertEqual(found[other].reason, "they have no sentence in common")
        status, out, _ = run("overlap", "made-up-lighthouse", "quiet")
        self.assertEqual(status, 1)
        self.assertIn("  shared  nothing to rest an offset on: /made-up/quiet.wav has no transcript", out)
        self.assertEqual(run("overlap", "made-up-lighthouse")[0], 0)

    def test_a_take_of_one_recording_has_nothing_to_compare(self):
        hand.remove(self.take, "collar")
        status, out, _ = run("overlap", "made-up-lighthouse")
        self.assertEqual((status, out), (1, "made-up-lighthouse has one recording: there is nothing to compare it with\n"))
        self.assertEqual(run("overlap", "made-up-nowhere")[0], 1)


@needs_ffmpeg
class PullTest(DatabaseTest):
    """A made-up rehearsal: a camera, and recorders that started at other times or not at all."""

    def setUp(self):
        super().setUp()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name).resolve()
        self.settings = settings_in(self.folder)
        ((self.take_id,),) = self.query(
            "INSERT INTO media.takes (name, project) VALUES ('made-up rehearsal', 'made-up') RETURNING id")
        self.camera = self.member(make_video(self.folder / "in" / "wide.mov", seconds=2.0), "camera", "wide",
                                  medium="video", primary=True)
        # Started two seconds after the camera.
        self.desk = self.member(make_tone(self.folder / "in" / "desk.wav", 6.0), "board", "desk", offset=2.0)
        self.take = takes.find("made-up rehearsal")

    def member(self, path, role, label, *, medium="audio", seconds=6.0, primary=False, offset=0.0, method="words",
               sound=True):
        ((asset_id,),) = self.query(
            """INSERT INTO media.assets (medium, path, content_hash, has_audio, project, duration_s)
               VALUES (%s, %s, %s, %s, 'made-up', %s) RETURNING id""",
            (medium, str(path), f"made-up-{Path(path).name}", sound, seconds))
        self.query(
            """INSERT INTO media.take_assets (take_id, asset_id, role, label, is_primary, offset_s, offset_method)
               VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING 1""",
            (self.take_id, asset_id, role, label, primary, offset, None if primary else method))
        return asset_id

    def pulled(self, at, seconds=3.0, **where):
        where = where or {"settings": self.settings}
        return {one.member.label: one for one in pull.pull(self.take, at, seconds, **where)}

    def test_one_moment_is_cut_from_every_recording_at_its_own_time(self):
        found = self.pulled(2.5)
        self.assertEqual((found["wide"].own_s, found["desk"].own_s), (2.5, 0.5))
        self.assertEqual({one.path.name for one in found.values()},
                         {"made-up-rehearsal-2.50s-wide.wav", "made-up-rehearsal-2.50s-desk.wav"})
        for one in found.values():
            self.assertEqual(one.path.parent, self.folder / "work" / "pulls")
            made = ffmpeg.probe(one.path)
            self.assertEqual((made.medium, made.has_audio), ("audio", True))
            self.assertAlmostEqual(made.duration_s, 3.0, delta=0.05)
            with wave.open(str(one.path)) as sound:
                self.assertEqual((sound.getnchannels(), sound.getframerate()), (1, 48000))

    def test_each_file_answers_for_itself_with_the_offset_it_was_cut_under(self):
        found = self.pulled(2.5)
        made = derive.lineage(found["desk"].path)
        self.assertEqual((made.kind, made.tool, made.start_s, made.end_s), ("snippet", "media pull", 0.0, 3.0))
        self.assertEqual(made.params, {"take": "made-up rehearsal", "take_time_s": 2.5, "seconds": 3.0,
                                       "label": "desk", "offset_s": 2.0, "offset_method": "words"})
        self.assertEqual(made.sources, ("desk.wav",))
        self.assertEqual(derive.lineage(found["wide"].path).params["offset_method"], "primary")
        self.assertEqual(self.query(
            """SELECT s.source_id, s.role, s.start_s, s.end_s, o.project
                 FROM media.derivation_sources s JOIN media.derivations d ON d.id = s.derivation_id
                 JOIN media.assets o ON o.id = d.output_id WHERE o.path = %s""", (str(found["desk"].path),)),
            [(self.desk, "sound", 0.5, 3.5, "made-up")])
        status, out, _ = run("lineage", str(found["desk"].path))
        self.assertEqual(status, 0)
        self.assertIn("snippet made by `media pull`", out)
        self.assertRegex(out, r"offset_s +2\.0")
        # Pulled again after a correction, the file says what it was cut under now.
        hand.place(self.take, "desk", 1.0)
        again = self.pulled(2.5)["desk"]
        self.assertEqual(again.path, found["desk"].path)
        self.assertEqual((derive.lineage(again.path).params["offset_s"],
                          derive.lineage(again.path).params["offset_method"]), (1.0, "manual"))
        self.assertEqual(self.query("SELECT count(*) FROM media.assets WHERE path = %s", (str(again.path),)), [(1,)])

    def test_a_recording_that_started_part_way_through_is_silent_until_it_starts(self):
        found = self.pulled(1.0)
        self.assertEqual((found["wide"].own_s, found["desk"].own_s), (1.0, -1.0))
        with wave.open(str(found["desk"].path)) as sound:
            self.assertAlmostEqual(sound.getnframes() / 48000, 3.0, delta=0.05)
            before = sound.readframes(47000)
            sound.setpos(50000)
            after = sound.readframes(4800)
        self.assertEqual(set(before), {0})
        self.assertGreater(len(set(after)), 16)
        self.assertEqual(self.query(
            """SELECT s.start_s, s.end_s FROM media.derivation_sources s
                 JOIN media.derivations d ON d.id = s.derivation_id JOIN media.assets o ON o.id = d.output_id
                WHERE o.path = %s""", (str(found["desk"].path),)), [(0.0, 2.0)])

    def test_a_recording_that_cannot_give_the_moment_gives_no_file_and_says_why(self):
        self.member(make_tone(self.folder / "in" / "late.wav", 6.0), "lav", "late", offset=20.0)
        self.member(make_tone(self.folder / "in" / "early.wav", 6.0), "lav", "early", offset=-30.0)
        self.member(make_tone(self.folder / "in" / "loose.wav", 6.0), "ambient", "loose", method=None)
        self.member(self.folder / "in" / "mute.mov", "screen", "mute", medium="video", sound=False)
        self.member(self.folder / "in" / "lost.wav", "lav", "lost")
        self.member(make_tone(self.folder / "in" / "short.wav", 1.0), "lav", "short", seconds=60.0)
        found = self.pulled(2.5)
        self.assertEqual({label: one.reason for label, one in found.items() if one.path is None}, {
            "late": "not rolling then",
            "early": "not rolling then",
            "loose": "not placed: run `media sync`, or set its offset with `media take`",
            "mute": "has no sound",
            "lost": "its file is not there",
            "short": "nothing to hear there",
        })
        self.assertEqual(sorted(path.name for path in (self.folder / "work" / "pulls").iterdir()),
                         ["made-up-rehearsal-2.50s-desk.wav", "made-up-rehearsal-2.50s-wide.wav"])
        self.assertEqual(self.query("SELECT count(*) FROM media.derivations WHERE tool = 'media pull'"), [(2,)])

    def test_what_was_pulled_is_not_mistaken_for_a_recording(self):
        self.pulled(2.5)
        self.assertEqual(hand.recording("desk").asset_id, self.desk)

    def test_two_labels_that_would_share_a_file_name_do_not(self):
        twin = self.member(make_tone(self.folder / "in" / "desk-2.wav", 6.0), "board", "desk!", offset=2.0)
        found = self.pulled(2.5)
        self.assertEqual({found["desk"].path.name, found["desk!"].path.name},
                         {"made-up-rehearsal-2.50s-desk.wav", f"made-up-rehearsal-2.50s-desk-{str(twin)[:6]}.wav"})

    def test_the_command_says_where_each_recording_was_and_where_the_files_are(self):
        self.member(make_tone(self.folder / "in" / "late.wav", 6.0), "lav", "late", offset=20.0)
        into = self.folder / "listen"
        status, out, err = run("pull", "made-up rehearsal", "--at", "0:01", "--seconds", "3", "--to", str(into))
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(out.splitlines(), [
            "made-up rehearsal at 0:01.0 for 3s",
            "  wide          its 0:01.0      made-up-rehearsal-1.00s-wide.wav",
            "  desk          starts 1.0s in  made-up-rehearsal-1.00s-desk.wav",
            "  late          not rolling then",
            f"2 files in {into}; if the offsets are right, each says the same words",
        ])
        self.assertEqual(len(list(into.iterdir())), 2)

    def test_a_moment_nothing_was_rolling_for_is_an_error(self):
        status, out, _ = run("pull", "made-up rehearsal", "--at", "5:00", "--to", str(self.folder / "listen"))
        self.assertEqual(status, 1)
        self.assertIn("no recording of made-up rehearsal gave anything at 5:00.0", out)
        self.assertFalse((self.folder / "listen").exists())
        status, _, err = run("pull", "made-up rehearsal", "--at", "1", "--seconds", "0")
        self.assertEqual((status, err), (1, "media: a moment has to have some length: give --seconds more than nothing\n"))
        self.assertEqual(run("pull", "made-up rehearsal")[0], 2)
        self.assertEqual(run("pull", "made-up-nowhere", "--at", "1")[0], 1)


if __name__ == "__main__":
    unittest.main()
