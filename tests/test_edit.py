import os
import unittest
from unittest import mock

from constructive_media import takes
from constructive_media.edit import EditError, flags, passages, quality, said, scores
from constructive_media.edit.flags import Spoken
from constructive_media.edit.said import Line

from .support import TEST_DATABASE_VARIABLE, DatabaseTest, write_lines
from .test_cli import run

#: A made-up interview, in the take's time: when each line starts, who says it, and what.
TALK = (
    (10.0, "Ada", "So how long have you kept the light on the point?"),
    (14.0, "Ben", "Nearly thirty years, since the old keeper went ashore for good."),
    (18.0, "Ben", "The lamp was oil then, and we trimmed the wick by hand every night."),
    (23.0, "Ada", "Did you ever let it go dark?"),
    (26.0, "Ben", "Once, in the great storm, and I have never forgiven myself for it."),
    (45.0, "Ben", "Um, the boats came in close that winter, you know, and the"),
    (49.5, "Ben", "Off the record, the harbour master was asleep at his post."),
    (53.5, "Ben", "We we counted nine lanterns on the water that night and every one came home."),
)
PACE_S = 0.3
#: How many sentences that is: one of the lines trails off into the next.
SENTENCES = len(TALK) - 1


def line(start, text, who="Ada", recording="desk", logprob=None, no_speech=None):
    count = len(text.split())
    words = tuple((start + at * PACE_S, start + at * PACE_S + 0.25, word) for at, word in enumerate(text.split()))
    return Line(start, start + (count - 1) * PACE_S + 0.25, text, who, recording,
                logprob=logprob, no_speech=no_speech, words=words)


def talk(recording="desk", shift=0.0, **fields):
    return [line(start + shift, text, who, recording, **fields) for start, who, text in TALK]


def heard(*lines, logprob=None):
    """Lines of one recording, as the flag reader takes them."""
    return [Spoken(at, found.start, found.end, found.text, logprob, found.words)
            for at, found in enumerate(line(start, text) for start, text in lines)]


def flagged(*lines, **options):
    return [(flag.kind, flag.action, round(flag.start_s, 2), round(flag.end_s, 2))
            for flag in flags.read("desk", heard(*lines), **options)]


class SaidTest(unittest.TestCase):
    def test_words_are_compared_without_case_or_punctuation(self):
        self.assertEqual(said.tokens("Don’t STOP — the lamp, please."), ["don't", "stop", "the", "lamp", "please"])
        self.assertEqual(said.telling("We trimmed the wick by hand"), {"trimmed", "wick", "hand"})

    def test_fillers_are_sounds_and_phrases_that_say_nothing(self):
        self.assertEqual(said.fillers(said.tokens("Um, the boats, you know, came in. I mean, uh, close.")), 4)
        self.assertEqual(said.fillers(said.tokens("You could not know what I had in mind.")), 0)

    def test_one_line_heard_by_two_microphones_is_told_once(self):
        desk, collar = talk("desk"), talk("collar", shift=0.08)
        collar[1] = line(14.08, "Nearly thirty years since the old keeper went ashore", "Ben", "collar")
        told = said.once(desk + collar)
        self.assertEqual([found.text for found in told], [text for _, _, text in TALK])
        self.assertEqual(said.talked_over(told), 0.0)

    def test_two_people_talking_at_once_are_both_kept(self):
        asked = line(10.0, "So how long have you kept the light on the point?", "Ada", "desk")
        over = line(10.6, "Nearly thirty years since the old keeper went ashore", "Ben", "collar")
        told = said.once([asked, over])
        self.assertEqual(len(told), 2)
        self.assertAlmostEqual(said.talked_over(told), asked.end - over.start)
        # One recording cannot talk over itself, whatever its timings say.
        self.assertEqual(said.talked_over([asked, line(10.6, over.text, "Ben", "desk")]), 0.0)

    def test_what_shares_time_with_a_stretch_is_found_without_asking_everything(self):
        told = said.Spans(reversed(talk()))
        self.assertEqual([found.start for found in told.within(14.5, 23.0)], [14.0, 18.0])
        self.assertEqual([found.start for found in told.within(30.0, 45.0)], [])
        # One long thing among short ones is still found from its far end.
        long = line(0.0, " ".join(["and"] * 200))
        self.assertIn(long, said.Spans([*talk(), long]).within(58.0, 59.0))
        self.assertEqual(said.Spans([]).within(0.0, 10.0), [])


class QualityTest(unittest.TestCase):
    def test_a_passage_is_complete_when_it_starts_and_ends_where_sentences_do(self):
        whole = quality.judge("Once, in the great storm. I have never forgiven myself for it.")
        self.assertTrue(whole.starts_complete and whole.ends_complete)
        self.assertFalse(quality.judge("The boats came in close that winter and the").ends_complete)
        self.assertFalse(quality.judge("The boats came in close to the.").ends_complete)
        self.assertFalse(quality.judge("and we trimmed the wick by hand.").starts_complete)
        self.assertFalse(quality.judge("So we trimmed the wick by hand.").starts_complete)
        self.assertEqual(quality.judge("").coherence, quality.judge("  ").coherence)

    def test_saying_it_again_trailing_off_and_guessing_all_count_against_it(self):
        good = "Nearly thirty years, since the old keeper went ashore for good. The lamp was oil then."
        clear = quality.judge(good)
        self.assertGreater(clear.coherence, 0.8)
        self.assertEqual(clear.repetition, 0.0)
        looped = quality.judge("The lamp was oil then. " * 6)
        self.assertGreater(looped.repetition, 0.7)
        self.assertLess(looped.coherence, clear.coherence - 0.2)
        self.assertLess(quality.judge("Um. Yes. Uh, well. The, um. You know.").coherence, clear.coherence - 0.15)
        self.assertAlmostEqual(clear.coherence - quality.judge(good, confidence=0.0).coherence, 0.16)
        for judged in (clear, looped, quality.judge("")):
            self.assertTrue(0.0 <= judged.coherence <= 1.0)

    def test_the_quiet_either_side_is_kept_and_is_far_when_nothing_is_said_there(self):
        judged = quality.judge("Did you ever let it go dark?", head_gap_s=0.756, tail_gap_s=4000.0)
        self.assertEqual((judged.head_gap_s, judged.tail_gap_s), (0.76, quality.FAR))

    def test_the_recording_that_heard_it_best_speaks_for_the_recogniser(self):
        self.assertEqual(quality.certainty(None), 1.0)
        self.assertEqual(quality.certainty(-0.2), 1.0)
        self.assertAlmostEqual(quality.certainty(-0.7), 0.4)
        self.assertAlmostEqual(quality.certainty(-0.25, 0.5), 0.5)
        both = talk("camera", logprob=-0.9) + talk("collar", logprob=-0.4, no_speech=0.1)
        sure, logprob = quality.recognised(both, 10.0, 30.0)
        self.assertAlmostEqual(sure, 0.8 * 0.9)
        self.assertAlmostEqual(logprob, -0.4)
        self.assertIsNone(quality.recognised(talk(), 10.0, 30.0))
        self.assertIsNone(quality.recognised(both, 31.0, 44.0))
        self.assertEqual(quality.confidence(talk(), 10.0, 30.0), 1.0)
        # Each line counts for the time it spends inside the stretch.
        mixed = [line(0.0, "one two three four five", logprob=-0.4), line(2.0, "six seven eight nine ten", logprob=-1.0)]
        self.assertAlmostEqual(quality.recognised(mixed, 0.0, 2.0)[1], -0.4)
        self.assertLess(quality.recognised(mixed, 0.0, 4.0)[1], -0.6)


class FindingPassagesTest(unittest.TestCase):
    def test_the_same_span_is_the_same_passage(self):
        one = passages.passage_id("take", 10.0, 29.85, "gap")
        self.assertEqual(one, passages.passage_id("take", 10.001, 29.8549, "gap"))
        self.assertNotEqual(one, passages.passage_id("take", 10.0, 29.85, "sentence"))
        self.assertNotEqual(one, passages.passage_id("other", 10.0, 29.85, "gap"))

    def test_speech_is_cut_where_the_talking_stops(self):
        found = passages.groups(talk())
        self.assertEqual([len(group) for group in found], [5, 3])
        # A pause does not end a group that has not yet said enough...
        paused = [line(0.0, "Did you ever let it go dark?"), line(5.0, "Once, in the great storm."),
                  line(14.0, "And never again."), line(17.0, "Not once.")]
        self.assertEqual([len(group) for group in passages.groups(paused)], [3, 1])
        # ...but a long silence ends it whatever its length.
        self.assertEqual([len(group) for group in passages.groups(paused, apart_s=2.0)], [1, 1, 1, 1])
        self.assertEqual(passages.groups([]), [])

    def test_a_group_that_runs_long_is_cut_at_its_longest_pause(self):
        long = [line(start, "The lamp was oil then and we trimmed the wick.") for start in (0, 4, 8, 14, 18, 22, 26)]
        self.assertEqual([len(piece) for piece in passages.split(long, max_len_s=20.0)], [3, 4])
        self.assertEqual(passages.split(long, max_len_s=90.0), [long])
        # Never so near an end that a fragment is shaved off.
        lopsided = [line(start, "The lamp was oil then.") for start in (0, 9, 11, 13, 15, 24)]
        self.assertEqual([len(piece) for piece in passages.split(lopsided, max_len_s=20.0)], [2, 4])
        self.assertEqual(passages.split(long[:3], max_len_s=1.0), [long[:3]])

    def test_by_silence_finds_the_exchanges_and_describes_them(self):
        first, second = passages.by_silence(talk("camera", logprob=-0.9) + talk("collar", shift=0.05, logprob=-0.2))
        self.assertEqual((first.start, first.end), (10.0, 29.85))
        self.assertTrue(first.text.startswith("So how long") and first.text.endswith("forgiven myself for it."))
        self.assertEqual((first.n_turns, first.n_speakers, first.question_n, first.filler_n), (3, 2, 2, 0))
        self.assertEqual(first.n_words, 56)
        self.assertEqual((first.longest_gap_s, first.overlap_s), (0.95, 0.0))
        self.assertEqual((first.quality.head_gap_s, first.quality.tail_gap_s), (quality.FAR, 15.15))
        self.assertTrue(first.quality.ends_complete)
        self.assertFalse(first.quality.starts_complete)
        self.assertEqual((second.n_turns, second.n_speakers, second.filler_n), (0, 1, 2))
        self.assertEqual(second.quality.head_gap_s, 15.15)
        # Judged on the microphone that heard it best, so no worse than alone.
        alone, _ = passages.by_silence(talk("collar", logprob=-0.2))
        self.assertEqual(first.quality.coherence, alone.quality.coherence)
        far, _ = passages.by_silence(talk("camera", logprob=-0.9))
        self.assertLess(far.quality.coherence, alone.quality.coherence)

    def test_what_is_too_short_to_use_is_not_a_passage(self):
        self.assertEqual(len(passages.by_silence(talk(), min_len_s=15.0)), 1)
        self.assertEqual(passages.by_silence(talk()[:2]), [])
        self.assertEqual(passages.by_silence([]), [])

    def test_by_sentence_finds_each_sentence_once(self):
        ran_on = line(60.0, "We counted nine lanterns out there. Every one came home that night and nobody")
        found = passages.by_sentence(talk("camera") + talk("collar", shift=0.05) + [ran_on])
        self.assertEqual([p.text for p in found][:5], [text for _, _, text in TALK[:5]])
        # A line that trails off runs on into the next: where the recogniser
        # broke its lines is not where a sentence ends.
        self.assertEqual(found[5].text, f"{TALK[5][2]} {TALK[6][2]}")
        self.assertEqual(len(found), SENTENCES + 2)
        self.assertEqual([p.text for p in found][-2:], ["We counted nine lanterns out there.",
                                                         "Every one came home that night and nobody"])
        self.assertTrue(all(p.n_speakers == 1 and p.n_turns == 0 for p in found))
        self.assertEqual(found[1].quality.head_gap_s, 0.75)
        # A sentence nobody finishes is ended for them.
        endless = line(0.0, " ".join(["and"] * 60))
        self.assertEqual(len(passages.by_sentence([endless], max_len_s=6.0)), 3)
        self.assertEqual(passages.by_sentence([line(0.0, "Not once.")]), [])


class ReadingFlagsTest(unittest.TestCase):
    def test_the_same_flag_found_again_is_the_same_flag(self):
        self.assertEqual(flags.flag_id("desk", "filler", 1.0, 1.25), flags.flag_id("desk", "filler", 1.001, 1.2549))
        self.assertNotEqual(flags.flag_id("desk", "filler", 1.0, 1.25), flags.flag_id("desk", "repetition", 1.0, 1.25))

    def test_someone_asking_for_it_not_to_be_used_is_kept_away_from(self):
        self.assertEqual(flagged((49.5, "Off the record, the harbour master was asleep.")),
                         [("off_record", "avoid", 49.5, 51.85)])
        self.assertEqual(flagged((3.0, "Please don’t use that bit about the storm.")),
                         [("off_record", "avoid", 3.0, 5.35)])
        self.assertEqual(flagged((3.0, "He set a new record that year, off the coast.")), [])

    def test_a_line_the_recogniser_guessed_at_is_kept_away_from(self):
        (flag,) = flags.read("desk", heard((2.0, "Thanks for watching the lighthouse."), logprob=-1.5))
        self.assertEqual((flag.kind, flag.action, flag.severity), ("hallucination", "avoid", 0.5))
        self.assertEqual(list(flags.read("desk", heard((2.0, "Thanks for coming."), logprob=-1.1))), [])

    def test_fillers_are_cut_and_hedges_are_looked_at(self):
        found = flagged((45.0, "Um, the boats came in close, you know, that winter."))
        self.assertEqual(found, [("filler", "cut", 45.0, 45.25), ("filler", "review", 46.8, 47.35)])

    def test_a_stutter_is_cut_but_emphasis_is_left_alone(self):
        self.assertEqual(flagged((53.5, "We we counted nine lanterns.")), [("repetition", "cut", 53.75, 54.05)])
        self.assertEqual(flagged((1.0, "No, no. It was very very dark. Yeah, yeah.")), [])
        # Said again after a breath is said again on purpose.
        slow = [Spoken(0, 0.0, 3.0, "We. We counted.", None, ((0.0, 0.25, "We."), (1.0, 1.25, "We"), (1.3, 1.55, "counted.")))]
        self.assertEqual(list(flags.read("desk", slow)), [])

    def test_a_sentence_started_twice_is_looked_at(self):
        found = flagged((5.0, "I went up I went up the stairs and lit the lamp."))
        self.assertEqual(found, [("false_start", "review", 5.0, 5.9)])
        self.assertEqual(flagged((5.0, "It was it was very late.")), [("false_start", "review", 5.0, 5.6)])
        self.assertEqual(flagged((5.0, "Oh no, oh no.")), [])

    def test_dead_air_is_cut_unless_someone_else_was_talking(self):
        lines = ((10.0, "Did you ever let it go dark?"), (15.05, "Once, in the great storm."))
        self.assertEqual(flagged(*lines), [("long_pause", "cut", 12.05, 15.05)])
        self.assertEqual(flagged(*lines, min_pause_s=4.0), [])
        asked = []
        self.assertEqual(flagged(*lines, talked_through=lambda start, end: asked.append((start, end)) or True), [])
        self.assertEqual(asked, [(12.05, 15.05)])
        (flag,) = flags.read("desk", heard(*lines))
        self.assertEqual((flag.severity, flag.detail), (0.75, "3.0s of silence"))

    def test_every_flag_has_a_length(self):
        instant = [Spoken(0, 1.0, 1.0, "Um", None, ((1.0, 1.0, "Um"),))]
        (flag,) = flags.read("desk", instant)
        self.assertEqual((flag.start_s, flag.end_s), (1.0, 1.0 + flags.SHORTEST_S))


class Interview(DatabaseTest):
    """The made-up interview as a take: a camera across the room and a microphone on a collar."""

    def setUp(self):
        super().setUp()
        self.camera = self.asset("lighthouse.mov", medium="video")
        self.collar = self.asset("collar.wav")
        ((self.take_id,),) = self.query(
            "INSERT INTO media.takes (name, project) VALUES ('made-up-lighthouse', 'made-up') RETURNING id")
        self.join(self.camera, "camera", primary=True)
        # The collar microphone was started five seconds after the camera.
        self.join(self.collar, "lav", offset=5.0)
        write_lines(self.camera, [(start, text, {"speaker": who, "avg_logprob": -0.9}) for start, who, text in TALK])
        write_lines(self.collar, [(start - 5.0, text, {"speaker": who, "avg_logprob": -0.2})
                                  for start, who, text in TALK])
        self.take = takes.find("made-up-lighthouse")

    def asset(self, name, *, medium="audio", project="made-up"):
        ((asset_id,),) = self.query(
            """INSERT INTO media.assets (medium, path, content_hash, has_audio, project, duration_s)
               VALUES (%s, %s, %s, true, %s, 60) RETURNING id""",
            (medium, f"/made-up/{name}", f"made-up-{name}", project))
        return asset_id

    def join(self, asset_id, role, *, take_id=None, primary=False, offset=0.0, method="words"):
        self.query(
            """INSERT INTO media.take_assets (take_id, asset_id, role, is_primary, offset_s, offset_method)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING asset_id""",
            (take_id or self.take_id, asset_id, role, primary, offset, None if primary else method))

    def shot(self, asset_id, idx, start, end, **measured):
        ((shot_id,),) = self.query(
            """INSERT INTO media_video.shots (asset_id, idx, start_s, end_s, sharpness, subject_n)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
            (asset_id, idx, start, end, measured.get("sharpness"), measured.get("subject_n")))
        return shot_id

    def stored(self, method="gap"):
        return self.query("SELECT id, start_s, end_s FROM media_video.passages "
                          "WHERE take_id = %s AND method = %s ORDER BY start_s", (self.take_id, method))

    def score(self, subject_id, name):
        found = self.query("SELECT value, raw, tier, modality, producer FROM media_video.scores "
                           "WHERE subject_id = %s AND name = %s", (subject_id, name))
        return found[0] if found else None


class PassagesTest(Interview):
    def test_a_takes_speech_is_read_in_the_takes_time_under_each_speakers_name(self):
        heard = said.lines(self.take_id)
        self.assertEqual(len(heard), 2 * len(TALK))
        self.assertEqual({(found.start, found.who) for found in heard}, {(start, who) for start, who, _ in TALK})
        self.assertEqual({found.logprob for found in heard}, {-0.9, -0.2})
        self.assertEqual(heard[0].words, ())
        worded = said.lines(self.take_id, with_words=True)
        self.assertEqual([word for _, _, word in worded[0].words], TALK[0][2].split())
        self.assertAlmostEqual(worded[1].words[1][0], 10.3)

    def test_a_recording_nothing_has_placed_is_not_read(self):
        loose = self.asset("loose.wav")
        self.join(loose, "ambient", method=None)
        write_lines(loose, [(700.0, "Somebody else entirely, talking about the weather.")])
        self.assertEqual(len(said.lines(self.take_id)), 2 * len(TALK))

    def test_passages_are_stored_with_their_quality(self):
        done = passages.propose(self.take)
        self.assertEqual((done.take, done.method, done.passages, done.added, done.removed, done.held),
                         ("made-up-lighthouse", "gap", 2, 2, 0, 0))
        rows = self.query(
            """SELECT p.start_s, p.end_s, p.n_words, p.n_turns, p.n_speakers, p.question_n, p.filler_n,
                      q.ends_complete, q.starts_complete, q.tail_gap_s, q.coherence
                 FROM media_video.passages p JOIN media_video.passage_quality q ON q.passage_id = p.id
                WHERE p.take_id = %s ORDER BY p.start_s""", (self.take_id,))
        self.assertEqual(rows[0][:10], (10.0, 29.85, 56, 3, 2, 2, 0, True, False, 15.15))
        self.assertEqual(rows[1][:2], (45.0, 57.95))
        self.assertGreater(rows[0][10], 0.9)
        self.assertEqual(str(self.stored()[0][0]), passages.passage_id(self.take_id, 10.0, 29.85, "gap"))

    def test_proposing_again_finds_the_same_passages(self):
        passages.propose(self.take)
        before = self.stored()
        again = passages.propose(self.take)
        self.assertEqual((again.passages, again.added, again.removed), (2, 0, 0))
        self.assertEqual(self.stored(), before)

    def test_each_way_and_each_name_keeps_its_own(self):
        passages.propose(self.take)
        sentences = passages.propose(self.take, by="sentence")
        self.assertEqual((sentences.method, sentences.passages), ("sentence", SENTENCES))
        strict = passages.propose(self.take, name="strict", min_len_s=15.0)
        self.assertEqual((strict.method, strict.passages), ("strict", 1))
        self.assertEqual(len(self.stored()), 2)
        with self.assertRaisesRegex(EditError, "by silence or sentence"):
            passages.propose(self.take, by="paragraph")
        with self.assertRaisesRegex(EditError, "by sentence has no min_gap_s"):
            passages.propose(self.take, by="sentence", min_gap_s=2.0)

    def test_what_is_no_longer_found_goes_unless_someone_judged_it(self):
        passages.propose(self.take)
        (first, *_), (second, *_) = self.stored()
        self.query("INSERT INTO media_video.decisions (kind, passage_id, keep) "
                   "VALUES ('passage_keep', %s, true) RETURNING id", (second,))
        changed = passages.propose(self.take, min_len_s=15.0)
        self.assertEqual((changed.passages, changed.added, changed.removed, changed.held), (1, 0, 0, 1))
        self.assertEqual({row[0] for row in self.stored()}, {first, second})
        self.query("DELETE FROM media_video.decisions WHERE passage_id = %s RETURNING id", (second,))
        changed = passages.propose(self.take, min_len_s=15.0)
        self.assertEqual((changed.removed, changed.held), (1, 0))
        self.assertEqual({row[0] for row in self.stored()}, {first})
        # Ruling a passage out is a judgement too, and it has to outlive the
        # settings that found the passage.
        self.query("INSERT INTO media_video.exclusions (passage_id) VALUES (%s) RETURNING id", (first,))
        changed = passages.propose(self.take, min_len_s=25.0)
        self.assertEqual((changed.passages, changed.removed, changed.held), (0, 0, 1))


class FlagsTest(Interview):
    def kinds(self, asset_id=None):
        return [row[0] for row in self.query(
            "SELECT kind FROM media_video.flags WHERE asset_id = %s ORDER BY start_s, kind",
            (asset_id or self.collar,))]

    def test_a_scan_flags_every_recording_and_counts_what_it_found(self):
        found = flags.scan(project="made-up")
        self.assertEqual(dict(found), {"long_pause": 2, "filler": 4, "off_record": 2, "repetition": 2})
        self.assertEqual(self.kinds(), ["long_pause", "filler", "filler", "off_record", "repetition"])
        ((subject, start, end, action, producer),) = self.query(
            """SELECT f.subject_id = s.id, f.start_s, f.end_s, f.action, f.producer
                 FROM media_video.flags f JOIN media_video.segments s ON s.id = f.subject_id
                WHERE f.asset_id = %s AND f.kind = 'off_record'""", (self.collar,))
        # In the recording's own time, not the take's.
        self.assertEqual((subject, start, end, action, producer), (True, 44.5, 47.75, "avoid", "flags"))
        self.assertEqual(sum(flags.scan(project="somewhere-else").values()), 0)

    def test_scanning_again_changes_nothing(self):
        flags.scan()
        before = self.query("SELECT id, created_at FROM media_video.flags ORDER BY id")
        self.assertEqual(flags.scan(match="collar"), {"long_pause": 1, "filler": 2, "off_record": 1, "repetition": 1})
        self.assertEqual(self.query("SELECT id, created_at FROM media_video.flags ORDER BY id"), before)

    def test_a_ruling_is_kept_and_everything_else_is_redone(self):
        flags.scan()
        self.query("UPDATE media_video.flags SET accepted = false, decided_at = now() "
                   "WHERE asset_id = %s AND kind = 'repetition' RETURNING id", (self.collar,))
        # The transcript is corrected: the stutter and the filler are gone.
        self.query("DELETE FROM media_video.segments WHERE asset_id = %s AND start_s >= 40 RETURNING id",
                   (self.collar,))
        write_lines(self.collar, [(40.0, "The boats came in close that winter."),
                                  (48.5, "We counted nine lanterns on the water.")])
        flags.scan(match="collar")
        # Two silences now, and the camera heard someone talking through the second.
        self.assertEqual(self.kinds(), ["long_pause"])
        # A flag a person ruled on outlives the line it was about only if the
        # line does: here the line was replaced, and its flags went with it.
        flags.scan()
        self.query("UPDATE media_video.flags SET accepted = true WHERE asset_id = %s AND kind = 'filler' "
                   "AND action = 'cut' RETURNING id", (self.camera,))
        flags.scan(min_pause_s=60.0)
        self.assertEqual(self.kinds(self.camera), ["filler", "filler", "off_record", "repetition"])
        self.assertEqual(self.kinds(), [])
        self.query("UPDATE media_video.flags SET accepted = true WHERE asset_id = %s AND kind = 'repetition' "
                   "RETURNING id", (self.camera,))
        self.query("UPDATE media_video.words w SET word = 'They' FROM media_video.segments s "
                   "WHERE s.id = w.segment_id AND s.asset_id = %s AND w.word = 'We' RETURNING w.id", (self.camera,))
        flags.scan(min_pause_s=60.0)
        self.assertIn("repetition", self.kinds(self.camera))

    def test_one_persons_pause_is_anothers_turn(self):
        ((take_id,),) = self.query(
            "INSERT INTO media.takes (name, project) VALUES ('made-up-two-collars', 'made-up') RETURNING id")
        ada, ben, loose = self.asset("ada.wav"), self.asset("ben.wav"), self.asset("other.wav")
        self.join(ada, "lav", take_id=take_id, primary=True)
        self.join(ben, "lav", take_id=take_id, offset=-2.0)
        self.join(loose, "ambient", take_id=take_id, method=None)
        write_lines(ada, [(10.0, "Did you ever let it go dark?"), (20.0, "And after that winter?"),
                          (40.0, "Thank you for telling me.")])
        # Ben answers in Ada's first pause. Nobody speaks in her second,
        # except on a recording nothing has placed in the take.
        write_lines(ben, [(15.0, "Once, in the great storm.")])
        write_lines(loose, [(28.0, "Somebody else entirely, talking about the weather.")])
        flags.scan(match="/(ada|ben)\\.wav$")
        self.assertEqual(self.query("SELECT kind, start_s FROM media_video.flags WHERE asset_id = %s", (ada,)),
                         [("long_pause", 21.15)])

    def test_a_passage_holding_an_off_the_record_line_is_withheld_until_the_flag_is_dismissed(self):
        passages.propose(self.take)
        (first, *_), (second, *_) = self.stored()
        self.assertEqual(flags.withheld([first, second]), set())
        flags.scan()
        self.assertEqual(flags.withheld([first, second]), {str(second)})
        self.assertEqual(flags.withheld([]), set())
        self.query("UPDATE media_video.flags SET accepted = false WHERE kind = 'off_record' AND asset_id = %s "
                   "RETURNING id", (self.camera,))
        self.assertEqual(flags.withheld([first, second]), {str(second)})
        self.query("UPDATE media_video.flags SET accepted = false WHERE kind = 'off_record' RETURNING id")
        self.assertEqual(flags.withheld([first, second]), set())


class Scored(Interview):
    def setUp(self):
        super().setUp()
        passages.propose(self.take)
        (self.first, *_), (self.second, *_) = self.stored()

    def footage(self):
        """Shots on the camera, and a sharper one elsewhere in the project that nobody has looked into."""
        near = self.shot(self.camera, 0, 0.0, 20.0, sharpness=100.0, subject_n=1)
        wide = self.shot(self.camera, 1, 20.0, 60.0, sharpness=50.0, subject_n=0)
        other = self.shot(self.asset("harbour.mov", medium="video"), 0, 0.0, 8.0, sharpness=200.0)
        self.query("INSERT INTO media_video.signatures (shot_id, hue, value, sat_mean) "
                   "VALUES (%s, '{1}', '{1}', 0.3), (%s, '{1}', '{1}', 0.9) RETURNING shot_id", (near, wide))
        ((frame,),) = self.query(
            "INSERT INTO media_video.frames (shot_id, t_s) VALUES (%s, 5.0) RETURNING id", (near,))
        self.query("INSERT INTO media_video.detections (frame_id, class_name, confidence, cx, cy, w, h, model) "
                   "VALUES (%s, 'person', 0.9, 0.5, 0.5, 0.5, 0.8, 'made-up') RETURNING id", (frame,))
        return near, wide, other


class ScoresTest(Scored):
    def test_what_the_words_say_is_scored(self):
        written = scores.run("made-up")
        for name in ("coherence", "completeness", "exchange", "question_density", "speech_clarity",
                     "asr_confidence", "publishable", "cleanliness"):
            self.assertEqual(written[name], 2, name)
        self.assertEqual(sum(written.values()), 16)
        self.assertEqual(self.score(self.first, "completeness"), (0.75, None, 1, "text", "passage_quality"))
        self.assertEqual(self.score(self.second, "completeness")[0], 1.0)
        self.assertEqual(self.score(self.first, "exchange"), (1.0, 3.0, 1, "text", "passages"))
        self.assertEqual(self.score(self.second, "exchange")[0], 0.0)
        value, _, _, sense, producer = self.score(self.first, "speech_clarity")
        self.assertAlmostEqual(value, 1 - 0.95 / 3)
        self.assertEqual((sense, producer), ("audio", "passages"))
        ((coherence,),) = self.query(
            "SELECT coherence FROM media_video.passage_quality WHERE passage_id = %s", (self.first,))
        self.assertEqual(self.score(self.first, "coherence")[0], coherence)

    def test_the_recogniser_is_judged_on_the_recording_that_heard_it_best(self):
        scores.run()
        self.assertEqual(self.score(self.first, "asr_confidence"), (1.0, -0.2, 1, "audio", "transcript"))
        self.query("UPDATE media_video.segments SET avg_logprob = -0.7 WHERE asset_id = %s RETURNING id", (self.collar,))
        scores.recogniser()
        self.assertAlmostEqual(self.score(self.first, "asr_confidence")[0], 0.4)
        self.query("UPDATE media_video.segments SET avg_logprob = NULL RETURNING id")
        self.query("DELETE FROM media_video.scores WHERE name = 'asr_confidence' RETURNING id")
        self.assertEqual(scores.recogniser(), {})

    def test_flags_are_redone_and_what_they_leave_is_scored(self):
        scores.run()
        self.assertEqual(self.score(self.first, "publishable")[0], 1.0)
        self.assertEqual(self.score(self.first, "cleanliness")[:2], (1.0, 0.0))
        self.assertEqual(self.score(self.second, "publishable")[0], 0.0)
        # A filler and a stutter, each heard by both microphones and counted once.
        value, cut, _, sense, producer = self.score(self.second, "cleanliness")
        self.assertAlmostEqual(cut, 0.25 + 0.3)
        self.assertAlmostEqual(value, 1 - 0.55 / 12.95)
        self.assertEqual((sense, producer), ("audio", "flags"))
        self.query("UPDATE media_video.flags SET accepted = false WHERE kind IN ('off_record', 'repetition') "
                   "RETURNING id")
        scores.run()
        self.assertEqual(self.score(self.second, "publishable")[0], 1.0)
        self.assertAlmostEqual(self.score(self.second, "cleanliness")[1], 0.25)

    def test_the_picture_is_scored_by_the_best_camera_against_the_projects_sharpest(self):
        near, wide, other = self.footage()
        written = scores.run()
        self.assertEqual((written["picture_quality"], written["sharpness"], written["safe_over_audio"]), (2, 3, 2))
        sharpest = 100 + 0.9 * 100            # the 95th percentile of 50, 100 and 200
        value, raw, tier, sense, producer = self.score(self.first, "picture_quality")
        seen = (10.0 * 100 + 9.85 * 50) / 19.85
        self.assertAlmostEqual(raw, seen)
        self.assertAlmostEqual(value, seen / sharpest)
        self.assertEqual((tier, sense, producer), (2, "picture", "frames"))
        self.assertAlmostEqual(self.score(self.first, "subject_present")[0], 10.0 / 19.85)
        self.assertEqual(self.score(self.second, "subject_present")[0], 0.0)

        self.assertAlmostEqual(self.score(near, "sharpness")[0], 100 / sharpest)
        self.assertEqual(self.score(other, "sharpness")[0], 1.0)
        self.assertAlmostEqual(self.score(near, "colour_variety")[0], 0.6)
        self.assertEqual(self.score(wide, "colour_variety")[:2], (1.0, 0.9))
        self.assertEqual((self.score(near, "subject_present")[0], self.score(wide, "subject_present")[0]), (1.0, 0.0))
        self.assertEqual((self.score(near, "safe_over_audio")[0], self.score(wide, "safe_over_audio")[0]), (0.0, 1.0))
        # Nobody has looked for people in the other shot, and that is not the
        # same as nobody being in it.
        self.assertIsNone(self.score(other, "subject_present"))
        self.assertIsNone(self.score(other, "safe_over_audio"))
        self.assertIsNone(self.score(other, "colour_variety"))

    def test_scoring_again_replaces_and_one_project_is_scored_alone(self):
        self.footage()
        first = scores.run()
        ((count,),) = self.query("SELECT count(*) FROM media_video.scores")
        self.assertEqual(scores.run(), first)
        self.assertEqual(self.query("SELECT count(*) FROM media_video.scores"), [(count,)])
        self.assertEqual(sum(scores.run("somewhere-else").values()), 0)

    def test_a_second_two_flags_cover_is_cut_once(self):
        self.assertAlmostEqual(scores.covered([(2.0, 3.0), (2.5, 4.0), (6.0, 7.0)], 0.0, 10.0), 3.0)
        self.assertAlmostEqual(scores.covered([(2.0, 3.0), (2.0, 3.0), (2.2, 2.4)], 0.0, 10.0), 1.0)
        # Only what falls inside the stretch counts.
        self.assertAlmostEqual(scores.covered([(-5.0, 1.0), (9.5, 30.0)], 0.0, 10.0), 1.5)
        self.assertEqual(scores.covered([], 0.0, 10.0), 0.0)

    def test_flags_on_a_recording_nothing_has_placed_say_nothing_about_the_take(self):
        loose = self.asset("loose.wav")
        self.join(loose, "ambient", method=None)
        write_lines(loose, [(12.0, "Off the record, um, somebody else entirely is talking.")])
        scores.run()
        self.assertEqual(self.score(self.first, "publishable")[0], 1.0)
        self.assertEqual(self.score(self.first, "cleanliness")[0], 1.0)
        self.assertEqual(flags.withheld([self.first, self.second]), {str(self.second)})

    def test_scores_can_be_recorded_from_anywhere(self):
        written = scores.record([("passage", self.first, "liked", 1.7, 4.0, scores.JUDGED, "cross", "made-up"),
                                 ("passage", self.second, "liked", -0.5, None, scores.JUDGED, "cross", "made-up")])
        self.assertEqual(written, {"liked": 2})
        self.assertEqual(self.score(self.first, "liked"), (1.0, 4.0, 3, "cross", "made-up"))
        self.assertEqual(self.score(self.second, "liked")[0], 0.0)
        self.assertEqual(scores.record([]), {})


class RankingTest(Scored):
    def test_recipes_are_listed_and_asked_for_by_name(self):
        found = {recipe.name: recipe for recipe in scores.recipes()}
        self.assertEqual(found["interview"].subject, "passage")
        self.assertAlmostEqual(sum(found["interview"].weights.values()), 1.0)
        self.assertEqual(scores.recipe("broll").subject, "shot")
        with self.assertRaisesRegex(EditError, "no recipe is called 'made-up'"):
            scores.ranked("made-up")

    def test_a_ranking_says_how_much_it_had_to_go_on(self):
        scores.run()
        ranking = scores.ranked("interview")
        (entry,) = ranking.entries
        self.assertEqual((entry.subject_id, entry.source, entry.start_s, entry.end_s),
                         (self.first, "made-up-lighthouse", 10.0, 29.85))
        self.assertTrue(entry.text.startswith("So how long"))
        self.assertAlmostEqual(entry.coverage, 0.92)
        self.assertEqual((entry.modalities, entry.deepest), (2, 1))
        self.assertNotIn("picture_quality", entry.contributing)
        self.assertEqual(scores.ranked("interview", min_coverage=0.95).entries, [])
        self.footage()
        scores.run()
        (entry,) = scores.ranked("interview", min_coverage=0.95).entries
        self.assertAlmostEqual(entry.coverage, 1.0)
        self.assertEqual((entry.modalities, entry.deepest), (3, 2))
        self.assertEqual(scores.ranked("interview", project="somewhere-else").entries, [])

    def test_what_someone_asked_not_to_be_used_is_never_listed(self):
        scores.run()
        ranking = scores.ranked("interview")
        self.assertEqual(ranking.withheld, 1)
        self.assertNotIn(self.second, [entry.subject_id for entry in ranking.entries])
        # Not by its score: the flags are read as they stand when the ranking is made.
        self.query("UPDATE media_video.flags SET accepted = false WHERE kind = 'off_record' RETURNING id")
        ranking = scores.ranked("interview")
        self.assertEqual((ranking.withheld, len(ranking.entries)), (0, 2))
        self.query("INSERT INTO media_video.exclusions (passage_id, reason) VALUES (%s, 'made up') RETURNING id",
                   (self.second,))
        ranking = scores.ranked("interview")
        self.assertEqual((ranking.withheld, [entry.subject_id for entry in ranking.entries]), (0, [self.first]))

    def test_each_moment_is_listed_once_and_one_way_can_be_asked_for(self):
        passages.propose(self.take, by="sentence")
        scores.run()
        ranking = scores.ranked("interview", limit=50)
        spans = [(entry.start_s, entry.end_s) for entry in ranking.entries]
        for at, (start, end) in enumerate(spans):
            for other_start, other_end in spans[at + 1:]:
                shared = min(end, other_end) - max(start, other_start)
                self.assertLessEqual(shared, 0.3 * min(end - start, other_end - other_start))
        self.assertFalse(any("record" in entry.text for entry in ranking.entries))
        sentences = scores.ranked("interview", method="sentence", limit=3)
        self.assertEqual(len(sentences.entries), 3)
        self.assertTrue(all(entry.end_s - entry.start_s < 12 for entry in sentences.entries))
        self.assertEqual([entry.score for entry in sentences.entries],
                         sorted((entry.score for entry in sentences.entries), reverse=True))

    def test_shots_are_ranked_by_what_has_been_measured_of_them(self):
        near, wide, other = self.footage()
        scores.run()
        ranking = scores.ranked("broll")
        self.assertEqual([entry.subject_id for entry in ranking.entries], [wide, near])
        self.assertEqual(ranking.entries[0].source, "/made-up/lighthouse.mov")
        self.assertAlmostEqual(ranking.entries[0].coverage, 0.86)
        # One measurement of four is too little to set a shot beside the others.
        self.assertEqual([entry.subject_id for entry in scores.ranked("broll", min_coverage=0.3).entries][0], other)
        self.query("INSERT INTO media_video.exclusions (asset_id) VALUES (%s) RETURNING id", (self.camera,))
        self.assertEqual(scores.ranked("broll").entries, [])


class EditCommandsTest(Interview):
    def setUp(self):
        super().setUp()
        environment = mock.patch.dict(os.environ, {
            "CONSTRUCTIVE_MEDIA_HOME": "/made-up/home",
            "CONSTRUCTIVE_MEDIA_DATABASE_URL": os.environ[TEST_DATABASE_VARIABLE],
        })
        environment.start()
        self.addCleanup(environment.stop)

    def test_passages_are_proposed_per_take(self):
        self.assertEqual(run("passages", "--dry-run")[:2], (0, (
            "      2 passages  made-up-lighthouse  (2 new)\n"
            "2 passages in 1 takes; nothing was written\n")))
        self.assertEqual(self.stored(), [])
        self.assertEqual(run("passages", "made-up-lighthouse")[1].splitlines()[-1], "2 passages in 1 takes")
        status, out, _ = run("passages", "--project", "made-up", "--min-length", "15", "--max-length", "60")
        self.assertEqual((status, out.splitlines()[0]), (0, "      1 passages  made-up-lighthouse  (1 removed)"))
        status, out, _ = run("passages", "--by", "sentence")
        self.assertEqual((status, out.splitlines()[-1]), (0, f"{SENTENCES} passages in 1 takes"))
        self.assertEqual(run("passages", "no-such-take")[::2], (1, "me: no take is called 'no-such-take'; `me takes` lists them\n"))
        self.assertEqual(run("passages", "--by", "sentence", "--min-gap", "2")[::2],
                         (1, "me: finding passages by sentence has no min_gap_s to set\n"))

    def test_flags_are_counted_by_kind(self):
        self.assertEqual(run("flag", "--match", "collar")[:2], (0, (
            "      1  off record\n      2  filler\n      1  repetition\n      1  long pause\n5 flags\n")))
        self.assertEqual(run("flag", "--min-pause", "60")[1].splitlines()[-1], "8 flags")

    def test_scoring_and_ranking(self):
        run("passages")
        status, out, _ = run("score", "--project", "made-up")
        self.assertEqual(status, 0)
        self.assertIn("      2  asr confidence\n", out)
        self.assertEqual(out.splitlines()[-1], "16 scores")
        status, out, _ = run("rank", "interview", "--top", "5")
        self.assertEqual(status, 0)
        first, *rest = out.splitlines()
        self.assertRegex(first, r"^  0\.\d\d  made-up-lighthouse  0:10\.0-0:29\.9  92% measured  So how long have you")
        self.assertLessEqual(len(first), 150)
        self.assertEqual(rest, ["1 passages by interview",
                                "1 left out: someone in them asks for it not to be used"])
        self.assertEqual(run("rank", "made-up")[::2], (1, "me: no recipe is called 'made-up'; `me rank` lists them\n"))

    def test_rank_without_a_recipe_lists_them(self):
        status, out, _ = run("rank")
        self.assertEqual(status, 0)
        self.assertIn("interview: ranks passages.", out)
        self.assertIn("  coherence 0.34, asr confidence 0.16, exchange 0.14", out)
        self.assertIn("broll: ranks shots.", out)

    def test_status_counts_what_editing_has_stored(self):
        run("passages")
        run("score")
        out = run("status")[1]
        self.assertRegex(out, r"passages +2\n")
        self.assertRegex(out, r"flags +10\n")
        self.assertRegex(out, r"scores +16\n")
