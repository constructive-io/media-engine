import os
import unittest
from dataclasses import replace
from unittest import mock

from constructive_media import takes
from constructive_media.edit import EditError, assemble, decisions, passages, quality, scores, timelines
from constructive_media.edit.assemble import Style
from constructive_media.edit.said import Line, Spans
from constructive_media.edit.timelines import Clip
from constructive_media.takes import Member

from . import test_cut
from .support import TEST_DATABASE_VARIABLE, write_lines
from .test_cli import run
from .test_edit import TALK, talk

#: A style the made-up interview fits: it is a minute of talk, not an afternoon.
LOOSE = Style(min_words=20, min_passage_s=5.0, target_s=60.0)


def member(name, medium="audio", *, offset=0.0, length=60.0, primary=False, placed=True, sound=True):
    return Member(name, f"/made-up/{name}", "camera" if medium == "video" else "lav", medium, f"/made-up/{name}",
                  length, primary, offset, None, None if primary or not placed else "words", None, sound)


def spoken(start, who, *sentences):
    """Sentences said one after another from `start`, three quarters of a second apart."""
    lines = []
    for text in sentences:
        lines.append((start, who, text))
        start += (len(text.split()) - 1) * 0.3 + 0.25 + 0.75
    return lines


#: A second made-up interview, on a quay: two answers with a long quiet between them.
WALL = (
    *spoken(4.0, "Cy",
            "The harbour wall was built by my grandfather and his two brothers.",
            "They carried every stone down from the quarry on a cart.",
            "It has stood through sixty winters without losing a single one.",
            "My father mended it with his own hands after the flood.",
            "People walk out along it every evening to watch the boats."),
    *spoken(40.0, "Cy",
            "My mother sold fish from a barrow at the top of the slip every morning.",
            "She knew every boat by the sound of its engine before it rounded the point.",
            "Nobody on this coast could gut a herring faster than she could."),
)


class PiecesTest(unittest.TestCase):
    def test_the_ends_are_pulled_in_to_the_words_and_never_pushed_out(self):
        heard = Spans(talk("camera") + talk("collar", shift=0.1))
        first, last = assemble.tighten(heard, 8.0, 33.0, 0.5)
        # The collar heard the last word a tenth of a second later, and holds the end open for it.
        self.assertAlmostEqual(first, 9.5)
        self.assertAlmostEqual(last, 30.45)
        self.assertEqual(assemble.tighten(heard, 9.0, 30.2, 1.5), (9.0, 30.2))
        self.assertEqual(assemble.tighten(heard, 31.0, 44.0, 0.5), (31.0, 44.0))
        # A cut through the middle of a line stays where it was put.
        self.assertEqual(assemble.tighten(heard, 15.0, 20.0, 0.1), (15.0, 20.0))
        untimed = Spans([Line(10.0, 12.0, "No word of this was timed.", "Ada", "desk")])
        self.assertEqual(assemble.tighten(untimed, 5.0, 20.0, 1.0), (9.0, 13.0))

    def test_a_recording_covers_a_stretch_it_ran_all_the_way_through(self):
        late = member("late", offset=30.0, length=20.0)
        self.assertTrue(assemble.covers(late, 30.0, 50.0))
        self.assertFalse(assemble.covers(late, 29.0, 40.0))
        self.assertFalse(assemble.covers(late, 40.0, 51.0))
        self.assertTrue(assemble.covers(member("unmeasured", length=None), 0.0, 9000.0))
        self.assertFalse(assemble.covers(member("loose", placed=False), 10.0, 20.0))

    def test_the_main_camera_is_used_while_it_rolls_and_another_when_it_does_not(self):
        main = member("main", "video", primary=True, length=40.0)
        second = member("second", "video", offset=30.0)
        cast = [main, member("collar"), second]
        self.assertIs(assemble.camera(cast, 10.0, 30.0), main)
        self.assertIs(assemble.camera(cast, 31.0, 39.0), main)
        self.assertIs(assemble.camera(cast, 38.0, 45.0), second)
        self.assertIsNone(assemble.camera(cast, 25.0, 45.0))
        self.assertIsNone(assemble.camera([member("collar")], 10.0, 30.0))

    def test_words_count_for_as_much_as_the_recogniser_was_sure_of_them(self):
        heard = talk("camera", logprob=-0.9) + talk("collar", logprob=-0.2)
        found = assemble.sure(heard, 10.0, 30.0)
        self.assertAlmostEqual(found["collar"], 56.0)
        self.assertAlmostEqual(found["camera"], 56.0 * quality.certainty(-0.9))
        # A word is inside when its middle is.
        self.assertAlmostEqual(assemble.sure(heard, 10.0, 11.0)["collar"], 3.0)
        self.assertEqual(assemble.sure(heard, 31.0, 44.0), {})
        untimed = [Line(0.0, 10.0, "one two three four", "Ada", "desk")]
        self.assertAlmostEqual(assemble.sure(untimed, 5.0, 20.0)["desk"], 2.0)

    def test_a_microphone_is_heard_before_a_camera_and_the_surest_microphone_first(self):
        cast = [member("camera", "video", primary=True), member("collar"), member("desk")]
        heard = Spans(talk("camera", logprob=-0.1) + talk("collar", logprob=-0.5) + talk("desk", logprob=-0.8))
        self.assertEqual(assemble.microphone(cast, heard, 10.0, 30.0).asset_id, "collar")
        self.assertEqual(assemble.microphone(cast[::2], heard, 10.0, 30.0).asset_id, "desk")
        self.assertEqual(assemble.microphone(cast[:1], heard, 10.0, 30.0).asset_id, "camera")
        self.assertIsNone(assemble.microphone(cast, heard, 31.0, 44.0))
        # One that was not running the whole way through is no use, and neither is one with no sound.
        started_late = [cast[0], member("collar", offset=15.0), cast[2]]
        self.assertEqual(assemble.microphone(started_late, heard, 10.0, 30.0).asset_id, "desk")
        silent = [cast[0], member("collar", sound=False)]
        self.assertEqual(assemble.microphone(silent, heard, 10.0, 30.0).asset_id, "camera")

    def test_measured_noise_decides_only_when_every_microphone_was_measured(self):
        cast = [member("camera", "video", primary=True), member("collar"), member("desk")]
        heard = Spans(talk("camera") + talk("collar", logprob=-0.5) + talk("desk", logprob=-0.8))
        measured = {"collar": 12.0, "desk": 30.0}
        self.assertEqual(assemble.microphone(cast, heard, 10.0, 30.0, measured).asset_id, "desk")
        self.assertEqual(assemble.microphone(cast, heard, 10.0, 30.0, {"desk": 30.0}).asset_id, "collar")
        # The camera is not in the running, so it does not have to have been measured.
        self.assertNotIn("camera", measured)

    def test_noise_is_averaged_over_the_time_each_measurement_shares_with_the_stretch(self):
        measured = Spans([assemble._Measured(0.0, 10.0, "collar", 20.0), assemble._Measured(10.0, 20.0, "collar", 40.0),
                          assemble._Measured(0.0, 20.0, "desk", 15.0)])
        self.assertEqual(assemble.clean(measured, 5.0, 15.0), {"collar": 30.0, "desk": 15.0})
        self.assertAlmostEqual(assemble.clean(measured, 5.0, 20.0)["collar"], 100.0 / 3)
        self.assertEqual(assemble.clean(measured, 20.0, 30.0), {})

    def test_clips_brought_along_keep_their_places(self):
        self.assertEqual(assemble._weave([(1, "B")], ["a", "c"]), ["a", "B", "c"])
        self.assertEqual(assemble._weave([(0, "A"), (3, "D")], ["b", "c"]), ["A", "b", "c", "D"])
        self.assertEqual(assemble._weave([(5, "Z")], ["a"]), ["a", "Z"])
        self.assertEqual(assemble._weave([], []), [])

    def test_places_are_spread_evenly(self):
        self.assertEqual(assemble._spread([0, 1, 2, 3, 4, 5], 2), [1, 4])
        self.assertEqual(assemble._spread([0, 1, 2, 3], 4), [0, 1, 2, 3])
        self.assertEqual(assemble._spread([3], 4), [3])


class Talks(test_cut.Footage):
    """The made-up interview and the made-up reel, with a second interview to add when one is wanted."""

    def short(self, passage_id):
        return str(passage_id).replace("-", "")[:12]

    def wall(self):
        """The second interview: one camera on a quay, with two answers in it."""
        self.quay = self.asset("quay.mov", medium="video")
        ((take_id,),) = self.query(
            "INSERT INTO media.takes (name, project) VALUES ('made-up-harbour-wall', 'made-up') RETURNING id")
        self.join(self.quay, "camera", take_id=take_id, primary=True)
        write_lines(self.quay, [(start, text, {"speaker": who, "avg_logprob": -0.3}) for start, who, text in WALL])
        passages.propose(takes.find("made-up-harbour-wall"))
        scores.run()
        (self.built, *_), (self.fish, *_) = self.query(
            "SELECT id FROM media_video.passages WHERE take_id = %s ORDER BY start_s", (take_id,))

    def where(self, made):
        return [(part.take, part.start_s, part.end_s) for part in made.parts]

    def why(self, made):
        return {str(left.passage_id): left.why for left in made.left}


class InterviewTest(Talks):
    def test_a_passage_is_seen_through_the_camera_and_heard_through_the_collar(self):
        made = assemble.interview("made-up-talk", LOOSE, take="made-up-lighthouse", title="The Light")
        (part,) = made.parts
        self.assertEqual((part.passage_id, part.take, part.start_s, part.end_s, part.picture, part.sound, part.kept),
                         (self.first, "made-up-lighthouse", 10.0, 29.85, "lighthouse.mov", "collar.wav", False))
        self.assertGreater(part.score, 0.9)
        # The collar was started five seconds after the camera, so its own time is five seconds behind.
        (clip,) = made.timeline.clips
        self.assertEqual((clip.picture, clip.start_s, clip.end_s, clip.sound, clip.sound_start_s, clip.sound_end_s),
                         (self.camera, 10.0, 29.85, self.collar, 5.0, 24.85))
        self.assertTrue(clip.label.startswith("So how long have you kept the light on the point? Nearly thirty"))
        self.assertEqual(len(clip.label), 120)
        self.assertEqual((made.considered, made.withheld, made.held, made.left), (1, 1, 0, []))
        cut = timelines.load("made-up-talk")
        self.assertEqual(cut, made.timeline)
        self.assertEqual((cut.kind, cut.project, cut.title, cut.revision, cut.style), ("cut", "made-up", "The Light", 1, None))
        self.assertEqual((cut.params["recipe"], cut.params["take"], cut.params["style"]["target_s"]),
                         ("interview", "made-up-lighthouse", 60.0))
        self.assertEqual(assemble.shaped("made-up-talk"), LOOSE)

    def test_what_does_not_suit_the_cut_is_passed_over_and_said_why(self):
        def refused(shape=LOOSE, **scope):
            with self.assertRaises(EditError) as raised:
                assemble.interview("made-up-talk", shape, **{"take": "made-up-lighthouse", **scope})
            return str(raised.exception)

        passed = "nothing to make 'made-up-talk' from: every passage looked at was passed over (1 {})"
        self.assertEqual(refused(Style()), passed.format("too few words"))
        self.assertEqual(refused(replace(LOOSE, min_passage_s=30.0)), passed.format("too short"))
        self.assertEqual(refused(replace(LOOSE, max_passage_s=10.0)), passed.format("too long"))
        self.query("UPDATE media_video.passage_quality SET ends_complete = false, tail_gap_s = 0.2 "
                   "WHERE passage_id = %s RETURNING passage_id", (self.first,))
        self.assertEqual(refused(), passed.format("starting or stopping in the middle of a sentence"))
        # A pause is somewhere to cut, whatever the sentence was doing.
        self.query("UPDATE media_video.passage_quality SET tail_gap_s = 0.5 WHERE passage_id = %s "
                   "RETURNING passage_id", (self.first,))
        self.assertEqual(len(assemble.interview("made-up-paused", LOOSE, take="made-up-lighthouse").parts), 1)
        self.query("UPDATE media.assets SET duration_s = 20 WHERE id = %s RETURNING id", (self.camera,))
        self.assertEqual(refused(), passed.format("with no camera rolling"))
        self.assertEqual(refused(take=None, project="made-up-nowhere"),
                         "nothing to make 'made-up-talk' from: recipe 'interview' ranks no passages here. "
                         "`me passages` finds them and `me score` scores them")
        self.assertEqual(refused(recipe="broll"), "recipe 'broll' ranks shots; an interview is cut from passages")
        self.assertEqual([cut.name for cut in timelines.listing()], ["made-up-paused"])

    def test_what_a_person_kept_goes_in_whatever_it_scored(self):
        decisions.keep(self.first, note="the storm", actor="made-up-editor")
        made = assemble.interview("made-up-talk", take="made-up-lighthouse")
        (part,) = made.parts
        # Too few words for the style, and in it all the same, with what was said about it.
        self.assertTrue(part.kept)
        self.assertGreater(part.score, 0.9)
        self.assertEqual(made.timeline.clips[0].note, "the storm")
        # Keeping it does not unsay what was asked for in it.
        decisions.keep(self.second, actor="made-up-editor")
        made = assemble.interview("made-up-talk", take="made-up-lighthouse")
        self.assertEqual(self.where(made), [("made-up-lighthouse", 10.0, 29.85)])
        self.assertEqual(self.why(made), {str(self.second): "holding something said off the record"})
        decisions.cut(self.first, actor="made-up-editor")
        with self.assertRaisesRegex(EditError, r"passed over \(1 holding something said off the record\)"):
            assemble.interview("made-up-talk", take="made-up-lighthouse")

    def test_quiet_at_the_ends_is_trimmed_to_what_the_style_allows(self):
        self.query("UPDATE media_video.passages SET start_s = 8.0, end_s = 33.0 WHERE id = %s RETURNING id",
                   (self.first,))
        made = assemble.interview("made-up-talk", replace(LOOSE, strip_silence_over_s=0.5),
                                  only=[self.short(self.first)])
        (clip,) = made.timeline.clips
        self.assertAlmostEqual(clip.start_s, 9.5)
        self.assertAlmostEqual(clip.end_s, 30.35)
        self.assertAlmostEqual(clip.sound_start_s, 4.5)
        self.assertAlmostEqual(clip.sound_end_s, 25.35)
        self.assertAlmostEqual(clip.duration_s, 20.85)

    def test_sound_comes_from_the_microphone_that_heard_it_best_or_measured_cleanest(self):
        def heard_through():
            made = assemble.interview("made-up-talk", LOOSE, take="made-up-lighthouse")
            return made.parts[0].sound, made.timeline.clips[0].sound_start_s

        desk = self.asset("desk.wav")
        self.join(desk, "board", offset=-2.0)
        write_lines(desk, [(start + 2.0, text, {"speaker": who, "avg_logprob": -0.6}) for start, who, text in TALK])
        self.assertEqual(heard_through(), ("collar.wav", 5.0))
        self.query("UPDATE media_video.segments SET avg_logprob = -0.9 WHERE asset_id = %s RETURNING id",
                   (self.collar,))
        self.assertEqual(heard_through(), ("desk.wav", 12.0))
        # Once both are measured, the cleaner one is used, however unsure its recogniser was.
        self.measure(self.collar, 30.0)
        self.assertEqual(heard_through(), ("desk.wav", 12.0))
        self.measure(desk, 12.0)
        self.assertEqual(heard_through(), ("collar.wav", 5.0))
        # A figure no microphone could have is a measurement that failed.
        self.query("UPDATE media_video.audio_quality SET snr_db = 90 WHERE segment_id IN "
                   "(SELECT id FROM media_video.segments WHERE asset_id = %s) RETURNING segment_id", (self.collar,))
        self.assertEqual(heard_through(), ("desk.wav", 12.0))
        # With no microphone the camera's own sound is used, and the clip says nothing of sound.
        self.query("DELETE FROM media.take_assets WHERE asset_id IN (%s, %s) RETURNING asset_id", (self.collar, desk))
        made = assemble.interview("made-up-talk", LOOSE, take="made-up-lighthouse")
        self.assertIsNone(made.parts[0].sound)
        self.assertEqual((made.timeline.clips[0].sound, made.timeline.clips[0].sound_start_s), (None, None))

    def measure(self, asset_id, snr_db):
        self.query("INSERT INTO media_video.audio_quality (segment_id, snr_db) "
                   "SELECT id, %s FROM media_video.segments WHERE asset_id = %s RETURNING segment_id",
                   (snr_db, asset_id))

    def test_a_cut_is_filled_to_its_length_in_the_order_things_were_said(self):
        self.wall()
        made = assemble.interview("made-up-talk", LOOSE, project="made-up")
        built, fish, light = [("made-up-harbour-wall", *self.span(self.built)),
                              ("made-up-harbour-wall", *self.span(self.fish)), ("made-up-lighthouse", 10.0, 29.85)]
        self.assertEqual(self.where(made), [built, fish, light])
        self.assertEqual([clip.picture for clip in made.timeline.clips], [self.quay, self.quay, self.camera])
        # The quay has one camera and no microphone: its clips are heard through it.
        self.assertEqual([part.sound for part in made.parts], [None, None, "collar.wav"])
        self.assertEqual((made.timeline.project, made.timeline.params["per_take"]), ("made-up", None))
        best = assemble.interview("made-up-best", replace(LOOSE, chronological=False), project="made-up")
        self.assertEqual([part.score for part in best.parts], sorted((part.score for part in best.parts), reverse=True))
        self.assertEqual(sorted(self.where(best)), [built, fish, light])
        # One take is not allowed to be the whole of a cut made from several.
        shared = assemble.interview("made-up-shared", LOOSE, project="made-up", per_take=1)
        self.assertEqual(len(shared.parts), 2)
        self.assertEqual(list(self.why(shared).values()), ["from a take that has given its share"])
        self.assertEqual(shared.timeline.params["per_take"], 1)

    def test_a_passage_that_would_overrun_is_passed_over_for_one_that_fits(self):
        self.wall()
        lengths = {name: end - start for name, (start, end) in
                   (("built", self.span(self.built)), ("fish", self.span(self.fish)))}
        self.assertGreater(lengths["built"], lengths["fish"] + 2.0)
        # The shorter answer is the one the recipe thinks least of, so it is come to last.
        self.query("UPDATE media_video.scores SET value = 0 WHERE subject_id = %s AND name = 'coherence' "
                   "RETURNING id", (self.fish,))
        ranked = scores.ranked("interview", limit=None, project="made-up").entries
        self.assertEqual([entry.subject_id for entry in ranked], [self.first, self.built, self.fish])
        # Room for the lighthouse and the shorter answer, and not for the longer.
        target = (19.85 + lengths["fish"]) / assemble.OVER + 0.1
        made = assemble.interview("made-up-talk", replace(LOOSE, target_s=target, chronological=False),
                                  project="made-up")
        self.assertEqual({part.passage_id for part in made.parts}, {self.first, self.fish})
        self.assertEqual(self.why(made), {str(self.built): "that would run past the length"})
        # Once it is long enough nothing more is looked at.
        short = assemble.interview("made-up-short", replace(LOOSE, target_s=15.0, chronological=False),
                                   project="made-up")
        self.assertEqual((len(short.parts), short.considered, short.left), (1, 1, []))

    def span(self, passage_id):
        ((start, end),) = self.query("SELECT start_s, end_s FROM media_video.passages WHERE id = %s", (passage_id,))
        return float(start), float(end)

    def test_passages_named_by_a_person_are_used_as_they_are_in_the_order_given(self):
        self.wall()
        named = [self.short(self.fish), self.short(self.first)]
        made = assemble.interview("made-up-talk", Style(target_s=5.0), only=named)
        self.assertEqual([part.passage_id for part in made.parts], [self.fish, self.first])
        self.assertEqual([(part.kept, part.score) for part in made.parts], [(True, None), (True, None)])
        self.assertEqual([clip.picture for clip in made.timeline.clips], [self.quay, self.camera])
        # Naming one twice, or one that holds what was asked not to be used, does not get it in.
        made = assemble.interview("made-up-talk", only=[*named, self.short(self.first), self.short(self.second)])
        self.assertEqual([part.passage_id for part in made.parts], [self.fish, self.first])
        self.assertEqual(self.why(made), {str(self.second): "holding something said off the record"})
        # A sentence from inside a passage that is already there is the same moment again.
        passages.propose(self.take, by="sentence")
        ((sentence,),) = self.query("SELECT id FROM media_video.passages WHERE take_id = %s AND method = 'sentence' "
                                    "AND start_s = 14.0", (self.take_id,))
        made = assemble.interview("made-up-talk", only=[self.short(self.first), self.short(sentence)])
        self.assertEqual(self.why(made), {str(sentence): "of a moment the cut already has"})
        with self.assertRaises(EditError):
            assemble.interview("made-up-talk", only=["0123456789ab"])

    def test_a_cut_made_after_another_keeps_what_was_kept_and_leaves_out_what_was_dropped(self):
        self.wall()
        assemble.interview("made-up-talk", LOOSE, project="made-up", title="The Light")
        timelines.react("made-up-talk", 1, "drop", note="heard it before")
        timelines.react("made-up-talk", 3, "adjust", shift_s=0.5, extend_s=-2.0)
        heard = timelines.feedback("made-up-talk")
        self.assertEqual(heard.spans, ((str(self.quay), *self.span(self.built)),))
        self.assertEqual(heard.dropped, frozenset())
        # Its scope, its style and its title come along without being said again.
        made = assemble.interview("made-up-talk-2", after="made-up-talk")
        fish, light = made.timeline.clips
        self.assertEqual((made.held, [part.passage_id for part in made.parts]), (1, [self.fish]))
        self.assertEqual(fish.picture, self.quay)
        # The sound moved with the picture: a clip that slid half a second is still in step.
        self.assertEqual((light.picture, light.start_s, light.end_s), (self.camera, 10.5, 28.35))
        self.assertEqual((light.sound, light.sound_start_s), (self.collar, 5.5))
        self.assertAlmostEqual(light.sound_end_s, 23.35)
        self.assertEqual(self.why(made), {str(self.built): "dropped from made-up-talk or a cut before it",
                                          str(self.first): "of a moment the cut already has"})
        cut = made.timeline
        self.assertEqual((cut.revision, cut.after, cut.title, cut.project), (2, "made-up-talk", "The Light", "made-up"))
        self.assertEqual(assemble.shaped("made-up-talk-2"), LOOSE)
        # What was dropped stays out of every cut after it.
        third = assemble.interview("made-up-talk-3", after="made-up-talk-2")
        self.assertEqual([part.passage_id for part in third.parts], [self.fish, self.first])
        self.assertEqual(third.timeline.revision, 3)

    def test_a_clip_is_not_adjusted_to_before_its_sound_began(self):
        timelines.save("made-up-early", [Clip(self.camera, 6.0, 12.0, sound=self.collar, sound_start_s=1.0,
                                              sound_end_s=7.0)])
        timelines.react("made-up-early", 1, "adjust", shift_s=-3.0)
        ((place, moved),) = timelines.feedback("made-up-early").held
        self.assertEqual((place, moved.start_s, moved.end_s, moved.sound_start_s, moved.sound_end_s),
                         (0, 5.0, 11.0, 0.0, 6.0))

    def test_a_stored_style_shapes_the_cut_and_is_remembered_with_it(self):
        self.assertEqual([style.name for style in assemble.styles()], ["thematic", "tiktok"])
        thematic = assemble.style("thematic")
        self.assertEqual((thematic.target_s, thematic.min_passage_s, thematic.max_passage_s, thematic.min_words,
                          thematic.strip_silence_over_s, thematic.chronological, thematic.broll_fraction),
                         (420, 20, 95, 80, 1.5, True, 0.25))
        self.assertFalse(assemble.style("tiktok").chronological)
        with self.assertRaisesRegex(EditError, "no style is called 'made-up-style'"):
            assemble.style("made-up-style")
        fitted = replace(thematic, min_words=20, min_passage_s=5.0)
        made = assemble.interview("made-up-talk", fitted, take="made-up-lighthouse")
        self.assertEqual((made.timeline.style, assemble.shaped("made-up-talk").min_words), ("thematic", 20))
        self.assertEqual([(cut.name, cut.style) for cut in timelines.listing()], [("made-up-talk", "thematic")])
        # A style nobody stored has no name to be found under.
        plain = assemble.interview("made-up-plain", replace(LOOSE, name="made-up-style"), take="made-up-lighthouse")
        self.assertIsNone(plain.timeline.style)


class CutawaysTest(Talks):
    def talking(self):
        """A cut of three clips: a long one, and two short enough to cover whole."""
        return timelines.save("made-up-talk", [
            Clip(self.camera, 10.0, 20.0, sound=self.collar, sound_start_s=5.0, sound_end_s=15.0, label="the light"),
            Clip(self.camera, 23.0, 28.0, label="the dark"),
            Clip(self.camera, 45.0, 49.0, sound=self.collar, sound_start_s=40.0, sound_end_s=44.0, label="the boats"),
        ], project="made-up", title="The Light", canvas="vertical", params={"style": {"broll_fraction": 1.0}})

    def shape(self, name):
        return [(self.named.get(str(clip.shot_id), clip.label), clip.start_s, clip.end_s,
                 clip.sound, clip.sound_start_s, clip.sound_end_s) for clip in timelines.load(name).clips]

    def test_footage_is_laid_over_the_middle_of_a_long_clip_and_the_sound_carries_on(self):
        assemble.interview("made-up-talk", LOOSE, take="made-up-lighthouse")
        made = assemble.cutaways("made-up-over", "made-up-talk")
        self.assertEqual((made.covered, made.wanted, made.available, made.likeness), (1, 1, 8, None))
        before, over, after = made.timeline.clips
        # Six seconds of the best shot, with the speaker seen for as long on either side of it.
        self.assertEqual((before.picture, before.start_s, after.end_s, after.picture), (self.camera, 10.0, 29.85, self.camera))
        self.assertAlmostEqual(before.end_s, 16.925)
        self.assertAlmostEqual(after.start_s, 22.925)
        self.assertEqual((over.picture, over.start_s, over.end_s, over.shot_id, over.label, over.note),
                         (self.harbour, 0.0, 6.0, self.shots["red-1"], "harbour.mov 0:00.0", "over what is being said"))
        # One unbroken run of the collar underneath all three.
        self.assertEqual([clip.sound for clip in made.timeline.clips], [self.collar] * 3)
        self.assertEqual((before.sound_start_s, after.sound_end_s), (5.0, 24.85))
        self.assertAlmostEqual(before.sound_end_s, over.sound_start_s)
        self.assertAlmostEqual(over.sound_end_s, after.sound_start_s)
        self.assertAlmostEqual(made.timeline.duration_s, 19.85)
        cut = made.timeline
        self.assertEqual((cut.kind, cut.revision, cut.after, cut.project), ("mixed", 2, "made-up-talk", "made-up"))
        self.assertEqual((cut.params["cutaways"]["fraction"], cut.params["recipe"]), (0.25, "interview"))

    def test_a_short_clip_is_covered_whole_and_each_cutaway_is_unlike_the_last(self):
        self.talking()
        made = assemble.cutaways("made-up-over", "made-up-talk")
        self.assertEqual((made.covered, made.wanted), (3, 3))
        self.assertEqual(self.shape("made-up-over"), [
            ("the light", 10.0, 12.0, self.collar, 5.0, 7.0),
            ("red-1", 0.0, 6.0, self.collar, 7.0, 13.0),
            ("the light", 18.0, 20.0, self.collar, 13.0, 15.0),
            # The camera's own sound, said outright now that the picture is something else.
            ("green", 40.5, 45.5, self.camera, 23.0, 28.0),
            ("red-2", 11.0, 15.0, self.collar, 40.0, 44.0),
        ])
        self.assertAlmostEqual(made.likeness, 0.4)
        cut = made.timeline
        self.assertEqual((cut.title, cut.canvas, cut.duration_s), ("The Light", "vertical", 19.0))
        # The first clip is never covered whole: whoever talks is seen first.
        timelines.save("made-up-brief", [Clip(self.camera, 10.0, 15.0), Clip(self.camera, 23.0, 28.0)])
        assemble.cutaways("made-up-brief-over", "made-up-brief", fraction=1.0)
        self.assertEqual([name for name, *_ in self.shape("made-up-brief-over")], [None, "red-1"])

    def test_footage_of_the_interview_itself_or_with_a_face_in_it_is_not_laid_over(self):
        self.talking()
        own = self.film(self.camera, 0, test_cut.RED, 0.99)
        self.named[str(own)] = "the interview"
        ((frame,),) = self.query(
            "INSERT INTO media_video.frames (shot_id, t_s) VALUES (%s, 3.0) RETURNING id", (self.shots["red-1"],))
        self.query("INSERT INTO media_video.detections (frame_id, class_name, confidence, cx, cy, w, h, model) "
                   "VALUES (%s, 'person', 0.9, 0.5, 0.5, 0.5, 0.8, 'made-up') RETURNING id", (frame,))
        decisions.cut(self.shots["green"], actor="made-up-editor")
        made = assemble.cutaways("made-up-over", "made-up-talk")
        self.assertEqual([name for name, *_ in self.shape("made-up-over") if not name.startswith("the ")],
                         ["red-2", "blue", "red-3"])
        self.assertEqual(made.available, 6)

    def test_a_cutaway_that_was_dropped_is_replaced_and_nothing_else_is_touched(self):
        self.talking()
        assemble.cutaways("made-up-over", "made-up-talk")
        before = self.shape("made-up-over")
        timelines.react("made-up-over", 4, "drop", note="too green")
        made = assemble.cutaways("made-up-over-2", "made-up-over")
        self.assertEqual((made.covered, made.wanted), (1, 1))
        after = self.shape("made-up-over-2")
        self.assertEqual(after[:3] + after[4:], before[:3] + before[4:])
        name, start, end, *sound = after[3]
        self.assertNotIn(name, ("green", "red-1", "red-2"))
        self.assertEqual((end - start, sound), (5.0, [self.camera, 23.0, 28.0]))
        self.assertEqual((made.timeline.revision, made.timeline.after), (3, "made-up-over"))
        # It stays out of the cuts after that one too.
        timelines.react("made-up-over-2", 4, "drop")
        assemble.cutaways("made-up-over-3", "made-up-over-2")
        self.assertNotIn(self.shape("made-up-over-3")[3][0], ("green", name, "red-1", "red-2"))

    def test_nothing_to_lay_over_a_cut_is_said_and_not_stored(self):
        self.talking()

        def refused(over="made-up-talk", **options):
            with self.assertRaises(EditError) as raised:
                assemble.cutaways("made-up-over", over, **options)
            return str(raised.exception)

        self.assertEqual(refused(fraction=2.0), "the share of clips to cover runs from 0 to 1, and 2 is outside it")
        self.assertEqual(refused(fraction=0.0), "nothing was laid over 'made-up-talk': it was asked to cover none of its clips")
        self.assertIn("ranks no shot that is long enough", refused(project="made-up-nowhere"))
        self.assertIn("shortest length", refused(shortest_s=8.0))
        select_cut = timelines.save("made-up-reel", [Clip(self.harbour, 0.0, 6.0, shot_id=self.shots["red-1"])])
        self.assertEqual(len(select_cut.clips), 1)
        self.assertEqual(refused("made-up-reel", fraction=1.0),
                         "nothing was laid over 'made-up-reel': none of its clips has room for a cutaway")
        self.assertEqual(sorted(cut.name for cut in timelines.listing()), ["made-up-reel", "made-up-talk"])


class AssembleCommandsTest(Talks):
    def setUp(self):
        super().setUp()
        environment = mock.patch.dict(os.environ, {
            "CONSTRUCTIVE_MEDIA_HOME": "/made-up/home",
            "CONSTRUCTIVE_MEDIA_DATABASE_URL": os.environ[TEST_DATABASE_VARIABLE],
        })
        environment.start()
        self.addCleanup(environment.stop)

    def test_styles_are_listed(self):
        status, out, _ = run("styles")
        self.assertEqual(status, 0)
        thematic, tiktok, count = out.splitlines()
        self.assertTrue(thematic.startswith(
            "thematic: 7:00.0 of passages 20 to 95s long with 80 words or more, in the order said, "
            "at most 1.5s of quiet at their ends, footage over 25% of the clips"))
        self.assertTrue(tiktok.startswith("tiktok: 1:00.0 of passages 4 to 14s long with 25 words or more, best first"))
        self.assertEqual(count, "2 styles")

    def test_an_interview_is_assembled_covered_and_revised(self):
        self.wall()
        loose = ("--min-words", "20", "--min-length", "5", "--target", "60")
        status, out, _ = run("assemble", "made-up-talk", "--project", "made-up", *loose, "--dry-run")
        self.assertEqual(status, 0)
        lines = out.splitlines()
        self.assertRegex(lines[0], r"^    1  0:04\.0-0:\d\d\.\d  \(\d\d\.\ds\)  quay\.mov  [01]\.\d\d  The harbour wall was built")
        self.assertRegex(lines[2], r"^    3  0:10\.0-0:29\.9  \(19\.9s\)  lighthouse\.mov with collar\.wav  [01]\.\d\d  So how long")
        self.assertRegex(lines[3], r"^3 clips, 0:\d\d\.\d of 1:00\.0, from 3 passages$")
        self.assertEqual(lines[4:], ["1 left out: someone in them asks for it not to be used", "nothing was written"])
        self.assertEqual(run("timelines")[1], "0 timelines\n")

        self.assertEqual(run("assemble", "made-up-talk", "--project", "made-up", *loose, "--style", "thematic")[0], 0)
        self.assertIn("  thematic style", run("timelines")[1])
        self.assertIn(", thematic style", run("timeline", "made-up-talk")[1].splitlines()[-1])
        self.assertEqual(run("keep", self.short(self.built), "--note", "the wall")[0], 0)
        self.assertEqual(run("note", "made-up-talk", "2", "drop")[0], 0)
        self.assertEqual(run("note", "made-up-talk", "3", "keep")[0], 0)
        status, out, _ = run("assemble", "made-up-talk-2", "--after", "made-up-talk", "--per-take", "3")
        self.assertEqual(status, 0)
        lines = out.splitlines()
        self.assertRegex(lines[0], r"quay\.mov  [01]\.\d\d  kept  The harbour wall")
        self.assertRegex(lines[1], r"^    2  0:10\.0-0:29\.9  \(19\.9s\)  from made-up-talk  So how long")
        self.assertRegex(lines[2], r"^2 clips, 0:\d\d\.\d of 1:00\.0, from 3 passages in the thematic style; "
                                   r"revision 2, after made-up-talk$")
        self.assertEqual(lines[3], "1 carried over from made-up-talk")
        self.assertEqual(lines[4], "2 passed over: 1 of a moment the cut already has, "
                                   "1 dropped from made-up-talk or a cut before it")

        status, out, _ = run("cutaways", "made-up-over", "--over", "made-up-talk-2", "--fraction", "1")
        self.assertEqual(status, 0)
        lines = out.splitlines()
        self.assertEqual(len(lines), 8)
        self.assertRegex(lines[1], r"^    2  0:00\.0-0:06\.0  \(6\.0s\)  harbour\.mov 0:00\.0$")
        self.assertRegex(lines[6], r"^6 clips, 0:\d\d\.\d; 2 of made-up-talk-2's have other footage over them, "
                                   r"from 8 shots by broll$")
        self.assertEqual(lines[7], "one cutaway is 0.40 like the next")

    def test_named_passages_and_what_cannot_be_done_are_reported(self):
        status, out, _ = run("assemble", "made-up-talk", "--passage", self.short(self.first))
        self.assertEqual(status, 0)
        self.assertRegex(out.splitlines()[0], r"lighthouse\.mov with collar\.wav  kept  So how long")
        status, _, err = run("assemble", "made-up-other", "--take", "made-up-lighthouse")
        self.assertEqual((status, err), (1, "me: nothing to make 'made-up-other' from: every passage looked at "
                                            "was passed over (1 too few words)\n"))
        status, _, err = run("assemble", "made-up-other", "--style", "made-up-style")
        self.assertEqual((status, err), (1, "me: no style is called 'made-up-style'; `me styles` lists them\n"))
        status, _, err = run("cutaways", "made-up-over", "--over", "made-up-talk", "--project", "made-up-nowhere")
        self.assertEqual(status, 1)
        self.assertIn("me: nothing was laid over 'made-up-talk'", err)
        status, out, _ = run("cutaways", "made-up-over", "--over", "made-up-talk", "--dry-run")
        self.assertEqual((status, out.splitlines()[-1]), (0, "nothing was written"))
        self.assertEqual(run("timelines")[1].splitlines()[-1], "1 timelines")
