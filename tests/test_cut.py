import os
import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from constructive_media.edit import EditError, decisions, scores, script, select, timelines
from constructive_media.edit.select import Options, Shot
from constructive_media.edit.timelines import Clip

from . import test_edit
from .support import TEST_DATABASE_VARIABLE
from .test_cli import run


def hue(*bins):
    """A picture whose colour is all in the named bins of twenty-four."""
    return [1 / len(bins) if at in bins else 0.0 for at in range(24)]


RED, GREEN, BLUE, GOLD, GREY = hue(0), hue(8), hue(16), hue(3), hue(*range(24))
MID = [0, 0, 0, 1.0, 0, 0, 0, 0]
#: A made-up reel of a harbour: (name, colour, how good). Four red shots score best.
REEL = (("red-1", RED, 0.95), ("red-2", RED, 0.94), ("red-3", RED, 0.93), ("red-4", RED, 0.92),
        ("green", GREEN, 0.90), ("blue", BLUE, 0.85), ("gold", GOLD, 0.80), ("grey", GREY, 0.60))


def shot(name, colour, quality, *, at=0.0, length=6.0, motion=10.0, peak=None, path="/made-up/harbour.mov"):
    return Shot(name, "harbour", path, at, at + length, quality, select.look(colour, MID, motion), peak)


def reel():
    return [shot(name, colour, quality, at=10.0 * index) for index, (name, colour, quality) in enumerate(REEL)]


def names(selection):
    return [clip.shot_id for clip in selection.clips]


class LikenessTest(unittest.TestCase):
    def test_a_shot_is_wholly_like_itself_and_little_like_another_colour(self):
        red, green = select.look(RED, MID, 10.0), select.look(GREEN, MID, 10.0)
        self.assertAlmostEqual(select.similarity(red, red), 1.0)
        self.assertAlmostEqual(select.similarity(red, green), 0.4)
        self.assertAlmostEqual(select.similarity(red, select.look(hue(0, 8), MID, 10.0)), 0.4 + 0.6 * 0.5 ** 0.5)

    def test_movement_is_compared_in_proportion_and_stillness_is_alike(self):
        def moving(one, other):
            return (select.similarity(select.look(RED, MID, one), select.look(RED, MID, other)) - 0.8) / 0.2

        self.assertAlmostEqual(moving(20.0, 20.0), 1.0)
        self.assertAlmostEqual(moving(20.0, 40.0), moving(40.0, 80.0), delta=0.06)
        self.assertLess(moving(5.0, 50.0), 0.2)
        # Two locked-off shots differ only in their noise.
        self.assertGreater(moving(0.2, 0.8), 0.85)
        self.assertGreater(moving(0.0, 0.0), 0.999)

    def test_neighbours_are_averaged_over_the_pairs_that_can_be_compared(self):
        red, green = select.look(RED, MID, 10.0), select.look(GREEN, MID, 10.0)
        self.assertAlmostEqual(select.adjacent([red, red, green]), 0.7)
        self.assertAlmostEqual(select.adjacent([red, None, red, red]), 1.0)
        self.assertIsNone(select.adjacent([red]))
        self.assertIsNone(select.adjacent([red, None]))


class ChoosingTest(unittest.TestCase):
    def test_the_top_of_the_list_is_the_same_shot_over_and_over(self):
        plain = select.choose(reel(), Options(count=4, diversity=0.0))
        self.assertEqual(names(plain), ["red-1", "red-2", "red-3", "red-4"])
        self.assertAlmostEqual(plain.likeness, 1.0)
        self.assertEqual((plain.considered, plain.unsigned, plain.held), (8, 0, 0))

    def test_picks_are_good_and_unalike_and_say_what_that_bought(self):
        chosen = select.choose(reel(), Options(count=4))
        self.assertEqual(names(chosen), ["red-1", "green", "blue", "gold"])
        self.assertAlmostEqual(chosen.likeness, 0.4)
        self.assertAlmostEqual(chosen.unselected, 1.0)

    def test_neighbours_are_the_ones_least_alike(self):
        chosen = select.choose(reel(), Options(count=6))
        # Three of the six are the same red shot, and no two of them are next to each other.
        self.assertEqual(names(chosen), ["red-1", "green", "blue", "red-2", "gold", "red-3"])
        self.assertLess(chosen.likeness, 0.41)

    def test_a_seed_gives_the_same_cut_each_time_and_another_seed_another(self):
        close = [shot(f"shot-{index}", hue(index), 0.5, at=10.0 * index) for index in range(24)]
        cuts = {seed: names(select.choose(close, Options(count=6), seed=seed)) for seed in range(6)}
        self.assertEqual(cuts[3], names(select.choose(close, Options(count=6), seed=3)))
        self.assertGreater(len({tuple(cut) for cut in cuts.values()}), 3)
        # The seed moves each shot by itself: taking one away leaves the rest where they were.
        without = [one for one in close if one.id != cuts[3][-1]]
        self.assertEqual(names(select.choose(without, Options(count=5), seed=3)), cuts[3][:-1])

    def test_an_excerpt_is_taken_around_the_sharpest_moment_and_stays_inside_the_shot(self):
        whole = shot("whole", RED, 0.9, at=100.0, length=20.0, peak=112.0)
        self.assertEqual(select.excerpt(whole, None), (100.0, 120.0))
        self.assertEqual(select.excerpt(whole, 4.0), (110.0, 114.0))
        self.assertEqual(select.excerpt(shot("early", RED, 0.9, at=100.0, length=20.0, peak=100.5), 4.0), (100.0, 104.0))
        self.assertEqual(select.excerpt(shot("late", RED, 0.9, at=100.0, length=20.0, peak=119.5), 4.0), (116.0, 120.0))
        self.assertEqual(select.excerpt(shot("unsampled", RED, 0.9, at=100.0, length=20.0), 4.0), (108.0, 112.0))
        self.assertEqual(select.excerpt(shot("short", RED, 0.9, at=100.0, length=3.0), 4.0), (100.0, 103.0))
        (clip,) = select.choose([whole], Options(count=1, excerpt_s=4.0)).clips
        self.assertEqual((clip.picture, clip.start_s, clip.end_s, clip.label), ("harbour", 110.0, 114.0, "harbour.mov 1:50.0"))

    def test_a_cut_can_follow_the_order_things_were_filmed_in(self):
        chosen = select.choose(reel(), Options(count=4, chronological=True, diversity=0.5))
        self.assertEqual([clip.start_s for clip in chosen.clips], [0.0, 40.0, 50.0, 60.0])

    def test_asking_for_more_than_there_is_gives_what_there_is(self):
        self.assertEqual(len(select.choose(reel()[:3], Options(count=12)).clips), 3)
        self.assertEqual(select.choose([], Options()).clips, [])
        with self.assertRaisesRegex(EditError, "at least one clip"):
            select.choose(reel(), Options(count=0))
        with self.assertRaisesRegex(EditError, "diversity runs from 0 to 1"):
            select.choose(reel(), Options(diversity=1.5))

    def test_picking_without_looking_at_every_shot_picks_what_looking_at_them_all_would(self):
        dice = random.Random(7)
        for trial in range(20):
            shots = [shot(f"shot-{index}", [dice.random() for _ in range(24)], dice.random(),
                          at=10.0 * index, motion=dice.uniform(0, 60)) for index in range(40)]
            shots = [Shot(one.id, one.asset_id, one.path, one.start_s, one.end_s, one.quality,
                          select.look([share / sum(one.look.hue) for share in one.look.hue], MID, one.look.motion))
                     for one in shots]
            diversity = dice.choice((0.2, 0.5, 0.9))
            picked, left = [], list(shots)
            while left and len(picked) < 10:
                best = max(left, key=lambda one: (one.quality - diversity * max(
                    (select.similarity(one.look, other.look) for other in picked), default=0.0)))
                picked.append(best)
                left.remove(best)
            chosen = select.choose(shots, Options(count=10, diversity=diversity, chronological=True))
            self.assertEqual(sorted(names(chosen)), sorted(one.id for one in picked), trial)


class Footage(test_edit.Scored):
    """The made-up reel, stored: eight shots of one file with a look and a score each."""

    def setUp(self):
        super().setUp()
        scores.run()
        self.harbour = self.asset("harbour.mov", medium="video")
        self.shots = {}
        for index, (name, colour, quality) in enumerate(REEL):
            self.shots[name] = self.film(self.harbour, index, colour, quality)
        self.named = {str(shot_id): name for name, shot_id in self.shots.items()}

    def film(self, asset_id, index, colour, quality, *, signed=True, people=1):
        shot_id = self.shot(asset_id, index, 10.0 * index, 10.0 * index + 6.0, sharpness=100.0, subject_n=people)
        if signed:
            self.query("INSERT INTO media_video.signatures (shot_id, hue, value, sat_mean, motion) "
                       "VALUES (%s, %s, %s, 0.5, 10.0) RETURNING shot_id", (shot_id, colour, MID))
        # Both of the scores the recipe needs, so that what it makes of the shot is `quality`.
        scores.record([("shot", shot_id, "sharpness", quality, None, 2, "picture", "made-up"),
                       ("shot", shot_id, "colour_variety", quality, None, 2, "picture", "made-up")])
        return shot_id

    def cut(self, name):
        return [self.named[str(clip.shot_id)] for clip in timelines.load(name).clips]


class TimelinesTest(Footage):
    def clips(self):
        return [Clip(self.harbour, 0.0, 4.0, shot_id=self.shots["red-1"], label="the harbour"),
                Clip(self.camera, 12.0, 20.0, sound=self.collar, sound_start_s=7.0, sound_end_s=13.0, note="answer")]

    def test_a_cut_is_stored_and_read_back_with_sound_from_somewhere_else(self):
        saved = timelines.save("made-up-cut", self.clips(), project="made-up", title="The Light", seed=4,
                               params={"why": "a test"}, canvas="vertical", fit="crop")
        self.assertEqual(saved, timelines.load("made-up-cut"))
        self.assertEqual((saved.kind, saved.status, saved.revision, saved.project, saved.title, saved.seed),
                         ("cut", "draft", 1, "made-up", "The Light", 4))
        self.assertEqual((saved.canvas, saved.fit, saved.params, saved.after), ("vertical", "crop", {"why": "a test"}, None))
        first, second = saved.clips
        self.assertEqual((first.picture, first.start_s, first.end_s, first.shot_id, first.label, first.sound),
                         (self.harbour, 0.0, 4.0, self.shots["red-1"], "the harbour", None))
        self.assertEqual((second.sound, second.sound_start_s, second.sound_end_s, second.note),
                         (self.collar, 7.0, 13.0, "answer"))
        # A clip plays for as long as its sound when the sound is its own.
        self.assertEqual((first.duration_s, second.duration_s, saved.duration_s), (4.0, 6.0, 10.0))

    def test_cuts_are_listed_newest_first_and_by_project_or_episode(self):
        timelines.save("made-up-cut", self.clips(), project="made-up")
        timelines.save("made-up-pilot", self.clips()[:1], project="elsewhere", episode="made-up-episode", kind="mixed")
        listed = {cut.name: cut for cut in timelines.listing()}
        self.assertEqual((listed["made-up-cut"].clips, listed["made-up-cut"].duration_s), (2, 10.0))
        self.assertEqual((listed["made-up-pilot"].kind, listed["made-up-pilot"].episode), ("mixed", "made-up-episode"))
        self.assertEqual([cut.name for cut in timelines.listing("made-up")], ["made-up-cut"])
        self.assertEqual([cut.name for cut in timelines.listing(episode="made-up-episode")], ["made-up-pilot"])

    def test_a_draft_is_replaced_and_a_cut_someone_has_ruled_on_is_not(self):
        timelines.save("made-up-cut", self.clips())
        self.assertEqual(len(timelines.save("made-up-cut", self.clips()[:1]).clips), 1)
        timelines.react("made-up-cut", 1, "keep")
        with self.assertRaisesRegex(EditError, "has reactions on its clips and is not written over"):
            timelines.save("made-up-cut", self.clips())
        timelines.save("made-up-final", self.clips())
        timelines.mark("made-up-final", "approved")
        self.assertEqual(timelines.load("made-up-final").status, "approved")
        with self.assertRaisesRegex(EditError, "'made-up-final' is approved and is not written over"):
            timelines.save("made-up-final", self.clips())
        self.assertEqual(len(timelines.load("made-up-final").clips), 2)

    def test_a_revision_knows_what_it_came_after(self):
        timelines.save("made-up-cut", self.clips())
        second = timelines.save("made-up-cut-2", self.clips(), after="made-up-cut")
        third = timelines.save("made-up-cut-3", self.clips(), after="made-up-cut-2")
        self.assertEqual((second.revision, second.after, third.revision, third.after),
                         (2, "made-up-cut", 3, "made-up-cut-2"))
        with self.assertRaisesRegex(EditError, "no timeline is called 'made-up-lost'"):
            timelines.save("made-up-cut-4", self.clips(), after="made-up-lost")
        with self.assertRaisesRegex(EditError, "cannot come after itself"):
            timelines.save("made-up-cut", self.clips(), after="made-up-cut")

    def test_a_cut_that_cannot_be_played_is_not_stored(self):
        with self.assertRaisesRegex(EditError, "has no clips"):
            timelines.save("made-up-cut", [])
        with self.assertRaisesRegex(EditError, "clip 3 of 'made-up-cut' ends before it starts"):
            timelines.save("made-up-cut", [*self.clips(), Clip(self.harbour, 9.0, 9.0)])
        with self.assertRaisesRegex(EditError, "no timeline is called 'made-up-cut'"):
            timelines.load("made-up-cut")
        with self.assertRaisesRegex(EditError, "one of cut, compilation, script, mixed, not 'montage'"):
            timelines.save("made-up-cut", self.clips(), kind="montage")
        with self.assertRaisesRegex(EditError, "not 'finished'"):
            timelines.mark("made-up-cut", "finished")

    def test_an_episode_begins_with_its_first_cut_and_is_not_made_up_elsewhere(self):
        with self.assertRaisesRegex(EditError, "no episode is called 'made-up-episode'"):
            timelines.episode_id("made-up-episode")
        timelines.save("made-up-cut", self.clips(), project="made-up", episode="made-up-episode")
        self.assertEqual(self.query("SELECT project FROM media.episodes WHERE id = %s",
                                    (timelines.episode_id("made-up-episode"),)), [("made-up",)])
        self.assertEqual(timelines.load("made-up-cut").episode, "made-up-episode")

    def test_the_last_thing_said_about_a_clip_is_what_stands(self):
        timelines.save("made-up-cut", self.clips())
        timelines.react("made-up-cut", 1, "drop", note="too dark")
        clip = timelines.react("made-up-cut", 1, "adjust", shift_s=0.5, extend_s=-1.0, note="late")
        self.assertEqual(clip.label, "the harbour")
        self.assertEqual(timelines.reactions("made-up-cut"), {1: timelines.Reaction("adjust", 0.5, -1.0, "late")})
        (listed,) = timelines.listing()
        self.assertEqual((listed.dropped, listed.adjusted), (0, 1))
        with self.assertRaisesRegex(EditError, "has clips 1 to 2, and no clip 3"):
            timelines.react("made-up-cut", 3, "keep")
        with self.assertRaisesRegex(EditError, "only `adjust` moves a clip"):
            timelines.react("made-up-cut", 2, "keep", shift_s=1.0)
        with self.assertRaisesRegex(EditError, "keep, drop or adjust, not 'love'"):
            timelines.react("made-up-cut", 2, "love")

    def test_what_was_said_is_what_the_next_cut_is_made_from(self):
        clips = [Clip(self.harbour, 10.0 * at, 10.0 * at + 6.0, shot_id=self.shots[name])
                 for at, name in enumerate(("red-1", "red-2", "red-3", "red-4"))]
        timelines.save("made-up-cut", clips)
        timelines.react("made-up-cut", 1, "drop")
        timelines.react("made-up-cut", 2, "keep")
        timelines.react("made-up-cut", 4, "adjust", shift_s=1.0, extend_s=-2.5)
        found = timelines.feedback("made-up-cut")
        self.assertEqual(found.dropped, {str(self.shots["red-1"])})
        self.assertEqual([(place, clip.shot_id, clip.start_s, clip.end_s) for place, clip in found.held],
                         [(1, self.shots["red-2"], 10.0, 16.0), (3, self.shots["red-4"], 31.0, 34.5)])
        # What an earlier cut dropped stays dropped in the cuts after it.
        timelines.save("made-up-cut-2", clips[1:], after="made-up-cut")
        timelines.react("made-up-cut-2", 2, "drop")
        self.assertEqual(timelines.feedback("made-up-cut-2").dropped,
                         {str(self.shots["red-1"]), str(self.shots["red-3"])})
        self.assertEqual(timelines.feedback("made-up-cut-2").held, ())


class CompilationTest(Footage):
    def test_a_compilation_is_stored_with_what_it_was_made_with(self):
        made = select.compilation("made-up-reel", count=4, project="made-up", seed=None, title="Harbour")
        self.assertEqual(self.cut("made-up-reel"), ["red-1", "green", "blue", "gold"])
        cut = made.timeline
        self.assertEqual((cut.kind, cut.project, cut.title, cut.revision), ("compilation", "made-up", "Harbour", 1))
        self.assertEqual(cut.params["options"]["count"], 4)
        self.assertAlmostEqual(cut.params["likeness"], 0.4)
        self.assertAlmostEqual(cut.params["unselected"], 1.0)
        self.assertEqual((made.selection.considered, made.selection.unsigned), (8, 0))
        self.assertEqual(cut.clips[0].label, "harbour.mov 0:00.0")

    def test_only_shots_that_can_be_compared_and_shown_are_considered(self):
        pier = self.asset("pier.mov", medium="video")
        self.film(pier, 0, RED, 0.99, signed=False)
        empty = self.film(pier, 1, GREEN, 0.99, people=0)
        black = self.film(pier, 2, BLUE, 0.99)
        self.query("UPDATE media_video.shots SET is_black = true WHERE id = %s RETURNING id", (black,))
        short = self.film(pier, 3, GOLD, 0.99)
        self.query("UPDATE media_video.shots SET end_s = start_s + 0.5 WHERE id = %s RETURNING id", (short,))
        made = select.compilation("made-up-reel", count=3, diversity=0.0)
        self.assertEqual([clip.shot_id for clip in made.timeline.clips][0], empty)
        self.assertEqual((made.selection.considered, made.selection.unsigned), (9, 1))
        made = select.compilation("made-up-people", count=3, diversity=0.0, people=True)
        self.assertEqual(self.cut("made-up-people"), ["red-1", "red-2", "red-3"])
        made = select.compilation("made-up-pier", match="pier", people=False)
        self.assertEqual([clip.shot_id for clip in made.timeline.clips], [empty])
        with self.assertRaisesRegex(EditError, "'\\(' is not a pattern"):
            select.compilation("made-up-reel", match="(")

    def test_nothing_to_choose_from_says_what_to_run(self):
        with self.assertRaisesRegex(EditError, "ranks no shots matching 'nowhere'. `me score`"):
            select.compilation("made-up-reel", match="nowhere")
        with self.assertRaisesRegex(EditError, "recipe 'interview' ranks passages"):
            select.compilation("made-up-reel", recipe="interview")
        with self.assertRaisesRegex(EditError, "no setting called colour"):
            select.compilation("made-up-reel", colour="red")

    def test_what_has_been_cut_is_not_chosen(self):
        decisions.cut(self.shots["green"])
        select.compilation("made-up-reel", count=4)
        self.assertEqual(self.cut("made-up-reel"), ["red-1", "blue", "gold", "red-2"])
        # A cut from one episode holds for that episode's cuts and no others.
        timelines.episode_id("made-up-episode", create=True)
        decisions.cut(self.shots["blue"], episode="made-up-episode")
        select.compilation("made-up-reel", count=4)
        self.assertIn("blue", self.cut("made-up-reel"))
        select.compilation("made-up-pilot", count=4, episode="made-up-episode")
        self.assertEqual(self.cut("made-up-pilot"), ["red-1", "gold", "red-2", "red-3"])
        self.assertEqual(timelines.load("made-up-pilot").episode, "made-up-episode")

    def test_a_revision_keeps_what_was_kept_and_replaces_what_was_dropped(self):
        select.compilation("made-up-reel", count=4, excerpt_s=4.0)
        self.assertEqual(self.cut("made-up-reel"), ["red-1", "green", "blue", "gold"])
        timelines.react("made-up-reel", 2, "drop")
        timelines.react("made-up-reel", 3, "adjust", shift_s=0.5, extend_s=1.0)
        timelines.react("made-up-reel", 4, "keep")
        second = select.compilation("made-up-reel-2", after="made-up-reel")
        # Kept and adjusted clips are where they were. The rest are chosen again around them.
        self.assertEqual(self.cut("made-up-reel-2"), ["red-1", "red-2", "blue", "gold"])
        self.assertEqual((second.timeline.revision, second.timeline.after, second.selection.held), (2, "made-up-reel", 2))
        self.assertEqual(second.options.excerpt_s, 4.0)
        blue = second.timeline.clips[2]
        self.assertEqual((blue.start_s, blue.end_s), (51.5, 56.5))
        # Dropped once, a shot stays out of every revision after.
        timelines.react("made-up-reel-2", 1, "drop")
        select.compilation("made-up-reel-3", after="made-up-reel-2", count=5)
        third = self.cut("made-up-reel-3")
        self.assertEqual(len(third), 5)
        self.assertFalse({"green", "red-1"} & set(third))
        self.assertEqual(timelines.load("made-up-reel-3").revision, 3)

    def test_settings_can_be_kept_under_a_name(self):
        select.remember("made-up-wide", Options(count=3, match="harbour", diversity=0.0), "Three of the harbour")
        select.remember("made-up-wide", Options(count=2, match="harbour", diversity=0.0), "Two of the harbour")
        kept = select.selector("made-up-wide")
        self.assertEqual((kept.options.count, kept.options.match, kept.description), (2, "harbour", "Two of the harbour"))
        self.assertIn("made-up-wide", [one.name for one in select.selectors()])
        made = select.compilation("made-up-reel", using="made-up-wide")
        self.assertEqual((self.cut("made-up-reel"), made.timeline.selector), (["red-1", "red-2"], "made-up-wide"))
        # What is asked for on the day goes over what the selector says.
        self.assertEqual(len(select.compilation("made-up-reel", using="made-up-wide", count=5).timeline.clips), 5)
        with self.assertRaisesRegex(EditError, "no selector is called 'made-up-lost'"):
            select.compilation("made-up-reel", using="made-up-lost")


class DecisionsTest(Footage):
    def setUp(self):
        super().setUp()
        self.query("UPDATE media_video.flags SET accepted = false WHERE kind = 'off_record' RETURNING id")

    def ruled(self):
        return self.query("SELECT kind, keep, context, source_of FROM media_video.decisions ORDER BY decided_at, id")

    def test_a_passage_or_a_shot_is_found_by_the_start_of_its_id(self):
        found = decisions.find(str(self.first)[:10])
        self.assertEqual((found.kind, found.id, found.where), ("passage", self.first, "made-up-lighthouse 0:10.0-0:29.9"))
        found = decisions.find(str(self.shots["blue"]).replace("-", "")[:14].upper())
        self.assertEqual((found.kind, found.id, found.where), ("shot", self.shots["blue"], "harbour.mov 0:50.0-0:56.0"))
        self.assertEqual(str(found), f"shot {str(self.shots['blue']).replace('-', '')[:12]} (harbour.mov 0:50.0-0:56.0)")
        with self.assertRaisesRegex(EditError, "'abc' is not an id"):
            decisions.find("abc")
        with self.assertRaisesRegex(EditError, "is not an id"):
            decisions.find("the red one")
        with self.assertRaisesRegex(EditError, "no passage's or shot's id starts with '000000000000'"):
            decisions.find("000000000000")
        with self.assertRaisesRegex(EditError, "no shot's id starts with"):
            decisions.find(self.first, "shot")

    def test_an_id_that_fits_two_things_is_not_guessed_at(self):
        spare = self.shot(self.harbour, 20, 300.0, 306.0)
        self.query("UPDATE media_video.shots SET id = %s WHERE id = %s RETURNING id",
                   (str(self.first)[:8] + str(spare)[8:], spare))
        with self.assertRaisesRegex(EditError, "more than one passage's or shot's id starts with"):
            decisions.find(str(self.first)[:8])
        self.assertEqual(decisions.find(str(self.first)[:8], "passage").id, self.first)

    def test_saying_the_same_thing_twice_is_recorded_once(self):
        self.assertTrue(decisions.keep(self.first, note="the best telling"))
        self.assertFalse(decisions.keep(self.first, note="the best telling"))
        self.assertTrue(decisions.keep(self.first, note="on second thoughts, the only telling"))
        self.assertTrue(decisions.keep(self.first, actor="someone-else", note="the best telling"))
        self.assertEqual([row[:3] for row in self.ruled()], [
            ("passage_keep", True, "the best telling"),
            ("passage_keep", True, "on second thoughts, the only telling"),
            ("passage_keep", True, "the best telling")])
        with self.assertRaisesRegex(EditError, "human, model or baseline, not 'a hunch'"):
            decisions.keep(self.first, source="a hunch")

    def test_a_cut_takes_a_thing_out_of_every_ranking_until_it_is_kept_again(self):
        self.assertEqual(len(scores.ranked("interview").entries), 2)
        self.assertTrue(decisions.cut(self.second, note="rambles"))
        self.assertFalse(decisions.cut(self.second, note="rambles"))
        self.assertEqual([entry.subject_id for entry in scores.ranked("interview").entries], [self.first])
        self.assertEqual(self.query("SELECT reason FROM media_video.exclusions"), [("rambles",)])
        self.assertTrue(decisions.keep(self.second))
        self.assertEqual(len(scores.ranked("interview").entries), 2)
        self.assertEqual([row[:2] for row in self.ruled()], [("passage_keep", False), ("passage_keep", True)])
        self.assertEqual(decisions.standing([self.first, self.second]), {str(self.second): (True, None)})

    def test_a_cut_from_one_episode_holds_only_there(self):
        with self.assertRaisesRegex(EditError, "no episode is called 'made-up-episode'"):
            decisions.cut(self.second, episode="made-up-episode")
        self.assertEqual(self.ruled(), [])
        timelines.episode_id("made-up-episode", create=True)
        decisions.cut(self.second, episode="made-up-episode")
        self.assertEqual(len(scores.ranked("interview").entries), 2)
        self.assertEqual(len(scores.ranked("interview", episode="made-up-other").entries), 2)
        self.assertEqual([entry.subject_id for entry in scores.ranked("interview", episode="made-up-episode").entries],
                         [self.first])
        # Keeping it in general does not undo a cut made for one episode.
        decisions.keep(self.second)
        self.assertEqual(len(scores.ranked("interview", episode="made-up-episode").entries), 1)
        decisions.keep(self.second, episode="made-up-episode", note="after all")
        self.assertEqual(len(scores.ranked("interview", episode="made-up-episode").entries), 2)

    def test_a_remark_is_neither_keep_nor_cut(self):
        self.assertTrue(decisions.remark(self.first, "trim", "lose the first question"))
        self.assertFalse(decisions.remark(self.first, "trim", "lose the first question"))
        self.assertEqual(self.ruled(), [("passage_keep", None, "[trim] lose the first question", "human")])
        self.assertEqual(len(scores.ranked("interview").entries), 2)

    def test_a_preference_is_between_two_of_a_kind(self):
        red, green = self.shots["red-1"], self.shots["green"]
        self.assertTrue(decisions.prefer(red, green, why="you can see the boats"))
        self.assertFalse(decisions.prefer(red, green, why="you can see the boats"))
        # Changing your mind is a new ruling, and so is changing it back.
        self.assertTrue(decisions.prefer(green, red))
        self.assertTrue(decisions.prefer(red, green, why="you can see the boats"))
        self.assertTrue(decisions.prefer(self.first, self.second, source="model"))
        self.assertEqual(self.query("SELECT kind, chosen_shot, rejected_shot, passage_id, rejected_passage, source_of "
                                    "FROM media_video.decisions ORDER BY decided_at, id"), [
            ("shot_pref", red, green, None, None, "human"), ("shot_pref", green, red, None, None, "human"),
            ("shot_pref", red, green, None, None, "human"),
            ("passage_pref", None, None, self.first, self.second, "model")])
        with self.assertRaisesRegex(EditError, "a shot and a passage cannot be compared"):
            decisions.prefer(red, self.first)
        with self.assertRaisesRegex(EditError, "the same thing twice"):
            decisions.prefer(red, red)
        self.assertEqual(decisions.tally(), {("passage_pref", "model"): 1, ("shot_pref", "human"): 3})
        latest = decisions.recent(2)
        self.assertEqual([(ruling.kind, ruling.subject, ruling.over) for ruling in latest],
                         [("passage_pref", str(self.first), str(self.second)), ("shot_pref", str(red), str(green))])

    def pairs(self, **options):
        return [(self.named[str(pair.one.id)], self.named[str(pair.other.id)])
                for pair in decisions.pairs("broll", **options)]

    def test_the_pairs_to_ask_about_are_the_ones_a_recipe_cannot_tell_apart(self):
        found = decisions.pairs("broll")
        self.assertEqual(self.pairs(), [("red-1", "red-2"), ("red-3", "red-4"), ("green", "blue"), ("gold", "grey")])
        self.assertAlmostEqual(found[0].gap, 0.01)
        self.assertAlmostEqual(found[0].likeness, 1.0)
        self.assertEqual(found[0].one.where, "harbour.mov 0:00.0-0:06.0")
        self.assertEqual(self.pairs(count=1), [("red-1", "red-2")])
        # A pair that has been judged, either way round, is not asked again.
        decisions.prefer(self.shots["red-2"], self.shots["red-1"])
        self.assertEqual(self.pairs()[:2], [("red-1", "red-3"), ("red-2", "red-4")])
        self.assertEqual(self.pairs(actor="someone-else")[0], ("red-1", "red-2"))

    def test_contrast_pairs_shots_that_score_alike_and_look_unalike(self):
        found = self.pairs(mode="contrast")
        self.assertEqual(found, [("red-1", "green"), ("red-2", "blue"), ("red-3", "gold"), ("red-4", "grey")])
        with self.assertRaisesRegex(EditError, "'interview' ranks passages"):
            decisions.pairs("interview", mode="contrast")
        with self.assertRaisesRegex(EditError, "close or contrast, not 'far'"):
            decisions.pairs("broll", mode="far")
        (pair,) = decisions.pairs("interview")
        self.assertEqual({pair.one.id, pair.other.id}, {self.first, self.second})
        self.assertIsNone(pair.likeness)


class ScriptTest(test_edit.Scored):
    def setUp(self):
        super().setUp()
        scores.run()

    def allow(self):
        self.query("UPDATE media_video.flags SET accepted = false WHERE kind = 'off_record' RETURNING id")

    def heading(self, passage_id):
        return f"## {str(passage_id).replace('-', '')[:12]}  made-up-lighthouse  "

    def test_the_ranking_is_written_out_in_the_order_it_was_said(self):
        self.allow()
        written = script.write(take="made-up-lighthouse")
        self.assertTrue(written.startswith("# made-up-lighthouse: the best 2 passages by interview\n\n0:32.8 in all."))
        self.assertLess(written.index(self.heading(self.first) + "0:10.0-0:29.9  (20s)"),
                        written.index(self.heading(self.second) + "0:45.0-0:58.0  (13s)"))
        self.assertIn("\n\nSo how long have you kept the light on the point? Nearly thirty years", written)
        self.assertEqual(written.count("\n>\n"), 2)
        self.assertEqual(script.write(limit=1).count("\n## "), 1)

    def test_what_someone_asked_not_to_be_used_is_counted_and_not_written(self):
        written = script.write()
        self.assertEqual(written.count("\n## "), 1)
        self.assertNotIn("harbour master", written)
        self.assertIn("\n1 more would be here, but someone in them asks for it not to be used.\n", written)

    def test_marks_are_read_back_as_decisions(self):
        self.allow()
        marked = (script.write()
                  .replace(">", "> KEEP — the best telling of this", 2).replace("> KEEP — the best telling of this", ">", 1)
                  .replace("\n>\n", "\n> cut: rambles\n"))
        self.assertIn("> KEEP", marked)
        done = script.read(marked)
        self.assertEqual((done.kept, done.cut, done.noted, done.unchanged, done.unmarked), (1, 1, 0, 0, 0))
        self.assertEqual(self.query("SELECT passage_id, keep, context FROM media_video.decisions ORDER BY decided_at"),
                         [(self.first, True, "the best telling of this"), (self.second, False, "rambles")])
        self.assertEqual([entry.subject_id for entry in scores.ranked("interview").entries], [self.first])

    def test_it_goes_round(self):
        self.allow()
        marked = script.write().replace("\n>\n", "\n> keep - the best telling\n", 1).replace("\n>\n", "\n> trim lose the um\n")
        self.assertEqual(script.read(marked).noted, 1)
        again = script.write()
        self.assertIn("\n> keep - the best telling\n", again)
        self.assertIn("\n> trim - lose the um\n", again)
        done = script.read(again)
        self.assertEqual((done.kept, done.cut, done.noted, done.unchanged), (0, 0, 0, 2))
        self.assertEqual(self.query("SELECT count(*) FROM media_video.decisions"), [(2,)])
        self.assertEqual(script.write(), again)

    def test_a_mark_that_is_not_understood_is_reported_and_nothing_is_guessed(self):
        self.allow()
        marked = script.write().replace("\n>\n", "\n> keeper\n", 1).replace("\n>\n", "\n> ? is this the storm year\n")
        marked += "\n## 0000000000ff  nowhere  0:00.0-0:05.0  (5s)\n\nNothing.\n\n> keep\n\n## 0000000000aa\n\n>\n"
        done = script.read(marked)
        self.assertEqual((done.kept, done.cut, done.noted, done.unmarked), (0, 0, 1, 1))
        self.assertEqual(done.unknown, ["0000000000ff"])
        self.assertEqual(done.unreadable, [(str(self.first).replace("-", "")[:12], "keeper")])
        self.assertEqual(self.query("SELECT passage_id, keep, context FROM media_video.decisions"),
                         [(self.second, None, "[?] is this the storm year")])

    def test_a_cut_made_for_one_episode_is_not_handed_back_as_a_cut_from_everything(self):
        self.allow()
        timelines.episode_id("made-up-episode", create=True)
        marked = script.write(episode="made-up-episode").replace("\n>\n", "\n> cut\n", 1)
        self.assertEqual(script.read(marked, episode="made-up-episode").cut, 1)
        self.assertEqual(script.write(episode="made-up-episode").count("\n## "), 1)
        everything = script.write()
        self.assertEqual((everything.count("\n## "), everything.count("\n>\n")), (2, 2))
        self.assertEqual(script.read(everything).unmarked, 2)

    def test_a_script_needs_ranked_passages(self):
        with self.assertRaisesRegex(EditError, "recipe 'broll' ranks shots; a script is made of passages"):
            script.write("broll")
        with self.assertRaisesRegex(EditError, "no ranked passages to write out. `me passages` finds them"):
            script.write(project="somewhere-else")


class CutCommandsTest(Footage):
    def setUp(self):
        super().setUp()
        environment = mock.patch.dict(os.environ, {
            "CONSTRUCTIVE_MEDIA_HOME": "/made-up/home",
            "CONSTRUCTIVE_MEDIA_DATABASE_URL": os.environ[TEST_DATABASE_VARIABLE],
        })
        environment.start()
        self.addCleanup(environment.stop)

    def short(self, name):
        return str(self.shots[name]).replace("-", "")[:12]

    def test_a_cut_is_compiled_reacted_to_and_revised(self):
        status, out, _ = run("compile", "made-up-reel", "--count", "4", "--excerpt", "4", "--dry-run")
        self.assertEqual((status, out), (0, (
            "    1  0:01.0-0:05.0  (4.0s)  harbour.mov 0:01.0\n"
            "    2  0:41.0-0:45.0  (4.0s)  harbour.mov 0:41.0\n"
            "    3  0:51.0-0:55.0  (4.0s)  harbour.mov 0:51.0\n"
            "    4  1:01.0-1:05.0  (4.0s)  harbour.mov 1:01.0\n"
            "4 clips, 0:16.0, from 8 shots by broll\n"
            "neighbours are 0.40 alike; the top 4 as they come would be 1.00\n"
            "nothing was written\n")))
        self.assertEqual(run("timelines")[1], "0 timelines\n")
        self.assertEqual(run("compile", "made-up-reel", "--count", "4", "--remember", "made-up-four")[0], 0)
        self.assertEqual(run("selectors")[1], "made-up-four: count 4\n")
        self.assertEqual(run("note", "made-up-reel", "2", "drop", "--note", "too green", "--never")[1],
                         "clip 2 of made-up-reel: drop; its shot is cut from everything\n")
        self.assertEqual(run("note", "made-up-reel", "3", "adjust", "--shift", "0.5", "--extend=-2")[0], 0)
        status, out, _ = run("timeline", "made-up-reel", "--status", "review")
        self.assertEqual((status, out.splitlines()[1:]), (0, [
            "    2  0:40.0-0:46.0  (6.0s)  harbour.mov 0:40.0  [drop: too green]",
            "    3  0:50.0-0:56.0  (6.0s)  harbour.mov 0:50.0  [adjust shift +0.5s extend -2s]",
            "    4  1:00.0-1:06.0  (6.0s)  harbour.mov 1:00.0",
            "made-up-reel: compilation, 4 clips, 0:24.0, review"]))
        status, out, _ = run("compile", "made-up-reel-2", "--after", "made-up-reel", "--seed", "5")
        self.assertEqual((status, out.splitlines()[-3:]), (0, [
            "4 clips, 0:22.0, from 7 shots by broll; revision 2, after made-up-reel",
            out.splitlines()[-2], "1 carried over from made-up-reel"]))
        self.assertNotIn("green", self.cut("made-up-reel-2"))
        self.assertEqual(self.cut("made-up-reel-2")[2], "blue")
        self.assertEqual(run("timelines", "--project", "made-up")[1], "0 timelines\n")
        self.assertEqual(run("timelines")[1].splitlines(), [
            "  made-up-reel-2  compilation  4 clips  0:22.0  draft  revision 2",
            "  made-up-reel  compilation  4 clips  0:24.0  review  (1 dropped, 1 adjusted)", "2 timelines"])
        self.assertEqual(run("timeline", "made-up-reel-2")[1].splitlines()[-1],
                         "made-up-reel-2: compilation, 4 clips, 0:22.0, draft, revision 2 after made-up-reel, seed 5")

    def test_what_cannot_be_done_is_said_plainly(self):
        self.assertEqual(run("compile", "made-up-reel", "--match", "nowhere")[::2], (
            1, "me: nothing to choose from: recipe 'broll' ranks no shots matching 'nowhere'. "
               "`me score` scores what has been analysed\n"))
        self.assertEqual(run("timeline", "made-up-lost")[::2],
                         (1, "me: no timeline is called 'made-up-lost'; `me timelines` lists them\n"))
        run("compile", "made-up-reel", "--count", "2")
        self.assertEqual(run("note", "made-up-reel", "1", "keep", "--never")[::2], (1, "me: --never goes with drop\n"))
        self.assertEqual(run("note", "made-up-reel", "9", "drop")[::2],
                         (1, "me: 'made-up-reel' has clips 1 to 2, and no clip 9\n"))
        self.assertEqual(run("note", "made-up-reel", "1", "love")[0], 2)
        status, out, _ = run("compile", "made-up-all", "--count", "20", "--project", "made-up")
        self.assertEqual((status, out.splitlines()[-1]), (0, "20 were asked for and there were not that many to choose from"))

    def test_rulings_are_made_by_id_and_counted(self):
        red, green = self.short("red-1"), self.short("green")
        self.assertEqual(run("cut", green, "--note", "too green")[1], f"cut: shot {green} (harbour.mov 0:40.0-0:46.0)\n")
        self.assertEqual(run("cut", green, "--note", "too green")[1].split(":")[0], "already cut")
        self.assertEqual(run("keep", green)[1], f"kept: shot {green} (harbour.mov 0:40.0-0:46.0)\n")
        self.assertEqual(run("prefer", red, green, "--why", "the boats")[1],
                         f"preferred: shot {red} (harbour.mov 0:00.0-0:06.0) over shot {green} (harbour.mov 0:40.0-0:46.0)\n")
        self.assertEqual(run("prefer", red, green, "--why", "the boats")[1].split(":")[0], "already preferred")
        self.assertEqual(run("cut", green, "--episode", "made-up-lost")[::2],
                         (1, "me: no episode is called 'made-up-lost'\n"))
        self.assertEqual(run("keep", "zzzzzz")[::2],
                         (1, "me: 'zzzzzz' is not an id: give at least the first 6 characters of one\n"))
        status, out, _ = run("decisions", "--recent", "1")
        self.assertEqual(status, 0)
        self.assertEqual(out.splitlines()[:3], ["      2  shot keep  (human)", "      1  shot pref  (human)", "3 decisions"])
        self.assertRegex(out.splitlines()[3], rf"^  \d{{4}}-\d\d-\d\d \d\d:\d\d  \S+  prefer  {red} over {green}  the boats$")

    def test_pairs_are_listed_to_be_answered(self):
        status, out, _ = run("pairs", "--count", "1")
        self.assertEqual((status, out), (0, (
            f"  {self.short('red-1')}  harbour.mov 0:00.0-0:06.0\n"
            f"  {self.short('red-2')}  harbour.mov 0:10.0-0:16.0  (0.010 apart in score, 1.00 alike)\n\n"
            "1 pairs; answer each with `me prefer <chosen> <rejected>`\n")))
        self.assertEqual(run("pairs", "--mode", "contrast", "--count", "2")[1].count("alike"), 2)

    def test_a_script_is_written_marked_and_read(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "script.md"
            self.assertEqual(run("script", "--take", "made-up-lighthouse", "--out", str(path))[1],
                             f"1 passages written to {path}\n")
            status, out, _ = run("script", "--top", "5")
            self.assertEqual((status, out), (0, path.read_text(encoding="utf-8").replace(
                "# made-up-lighthouse:", "# Script:")))
            path.write_text(path.read_text(encoding="utf-8").replace("\n>\n", "\n> keep - opens the film\n")
                            + "\n## 0000000000ff\n\n> keep\n", encoding="utf-8")
            self.assertEqual(run("script", "--read", str(path), "--dry-run")[:2], (1, (
                "1 kept, 0 cut, 0 noted; 0 already on record, 0 left unmarked; nothing was written\n"
                "  0000000000ff: not one passage's id, so its mark was not stored\n")))
            self.assertEqual(self.query("SELECT count(*) FROM media_video.decisions"), [(0,)])
            self.assertEqual(run("script", "--read", str(path))[0], 1)
            self.assertEqual(run("script", "--read", str(path))[1].splitlines()[0],
                             "0 kept, 0 cut, 0 noted; 1 already on record, 0 left unmarked")
            self.assertEqual(run("script", "--read", str(Path(folder) / "lost.md"))[::2],
                             (1, f"me: {Path(folder) / 'lost.md'} cannot be read: No such file or directory\n"))
