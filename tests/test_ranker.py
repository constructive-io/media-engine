import dataclasses
import itertools
import os
import random
import unittest
from unittest import mock

from constructive_media.edit import EditError, decisions, ranker, scores
from constructive_media.edit.ranker import Example, Model

from . import test_edit
from .support import TEST_DATABASE_VARIABLE
from .test_cli import run


def choices(count, truth, seed=7):
    """Made-up choices by somebody who goes by `truth`, more often than not: chosen less passed over."""
    dice = random.Random(seed)
    made = []
    for _ in range(count):
        apart = [dice.gauss(0, 1) for _ in truth]
        chance = ranker._sigmoid(sum(value * weight for value, weight in zip(apart, truth, strict=True)))
        made.append(apart if dice.random() < chance else [-value for value in apart])
    return made


def cost(rows, theta, penalty=0.01):
    return (sum(ranker._softplus(-ranker._dot(row, theta)) for row in rows) / len(rows)
            + penalty / 2 * sum(weight * weight for weight in theta))


def descent(rows, steps=3000, rate=0.2, penalty=0.01):
    """The same fit the slow way: a small step downhill, thousands of times."""
    theta = [0.0] * len(rows[0])
    for _ in range(steps):
        slope = [penalty * weight for weight in theta]
        for row in rows:
            wrong = ranker._sigmoid(-ranker._dot(row, theta))
            for at, value in enumerate(row):
                slope[at] -= wrong * value / len(rows)
        theta = [weight - rate * move for weight, move in zip(theta, slope, strict=True)]
    return theta


def model(accuracy, tested):
    return Model("made-up", ("sharpness",), (1.0,), (1.0,), tested, 1.0, accuracy, tested, "shoot")


def within(shoot, count, *, better=True):
    """Choices inside one made-up shoot, each between two shots of its one file."""
    return [Example((float(at + 1),) if better else (0.0,), (0.0,) if better else (float(at + 1),),
                    frozenset({shoot}), frozenset({f"/made-up/{shoot}.mov"})) for at in range(count)]


class FittingTest(unittest.TestCase):
    def test_the_fit_is_the_one_the_slow_way_reaches(self):
        rows = choices(120, [1.5, -1.0, 0.0, 0.5])
        quick, slow = ranker.fit(rows), descent(rows)
        for one, other in zip(quick, slow, strict=True):
            self.assertAlmostEqual(one, other, places=6)
        self.assertLessEqual(cost(rows, quick), cost(rows, slow) + 1e-12)

    def test_it_finds_what_the_chooser_went_by(self):
        found = ranker.fit(choices(600, [2.0, -1.0, 0.0]))
        self.assertGreater(found[0], 1.0)
        self.assertLess(found[1], -0.5)
        self.assertLess(abs(found[2]), 0.3)

    def test_the_opposite_choices_give_the_opposite_weights(self):
        rows = choices(80, [1.0, -2.0])
        for one, other in zip(ranker.fit(rows), ranker.fit([[-value for value in row] for row in rows]), strict=True):
            self.assertAlmostEqual(one, -other, places=9)

    def test_a_feature_that_decides_every_choice_does_not_run_away(self):
        dice = random.Random(3)
        found = ranker.fit([[abs(dice.gauss(0, 1)) + 0.1, dice.gauss(0, 1)] for _ in range(40)])
        self.assertTrue(1.0 < found[0] < 20.0)

    def test_a_feature_that_never_differs_weighs_nothing(self):
        theta, scale = ranker._fitted(
            [Example((2.0, 5.0), (1.0, 5.0), frozenset(), frozenset()) for _ in range(12)], 0.01)
        self.assertGreater(theta[0], 0)
        self.assertEqual((theta[1], scale[1]), (0.0, 1.0))

    def test_a_set_of_equations_is_solved(self):
        # Only the lower half is read: the upper is its mirror.
        solved = ranker._solve([[4.0, 0.0, 0.0], [2.0, 3.0, 0.0], [0.0, 1.0, 2.0]], [10.0, 12.0, 8.0])
        for found, expected in zip(solved, [1.5, 2.0, 3.0], strict=True):
            self.assertAlmostEqual(found, expected, places=12)

    def test_a_shots_standing_is_its_measurements_by_the_weights(self):
        made = Model("made-up", ("sharpness", "motion"), (2.0, -1.0), (10.0, 4.0), 40, 0.9)
        self.assertAlmostEqual(made.strength((50.0, 8.0)), 2.0 * 5.0 - 1.0 * 2.0)
        self.assertEqual(made.weights, [("sharpness", 2.0), ("motion", -1.0)])


class ProvingTest(unittest.TestCase):
    def test_better_than_a_coin_has_to_be_by_more_than_chance(self):
        self.assertFalse(model(None, 0).proven)
        self.assertFalse(model(0.50, 400).proven)
        # Six in ten of thirty is what a coin does one time in seven.
        self.assertFalse(model(0.60, 30).proven)
        self.assertTrue(model(0.70, 30).proven)
        self.assertTrue(model(0.60, 100).proven)
        self.assertFalse(model(0.40, 1000).proven)

    def test_each_shoot_is_held_out_in_turn(self):
        found = within("quay", 12) + within("market", 12) + within("hill", 12)
        folds, accuracy, tested = ranker._held_out(found, lambda example: example.shoots, 0.01)
        self.assertEqual([(fold.held_out, fold.pairs, fold.accuracy) for fold in folds],
                         [("hill", 12, 1.0), ("market", 12, 1.0), ("quay", 12, 1.0)])
        self.assertEqual((accuracy, tested), (1.0, 36))

    def test_what_one_shoot_taught_is_tested_on_another(self):
        # On the hill the less sharp shot was chosen every time, against the other two shoots.
        found = within("quay", 12) + within("market", 12) + within("hill", 12, better=False)
        folds, accuracy, tested = ranker._held_out(found, lambda example: example.shoots, 0.01)
        self.assertEqual({fold.held_out: fold.accuracy for fold in folds}["hill"], 0.0)
        self.assertLess(accuracy, 0.7)
        # Fitted to all of it, the same model would have looked better than it is.
        self.assertEqual(tested, 36)

    def test_a_choice_touching_the_held_out_shoot_is_never_fitted_to(self):
        across = [Example((1.0,), (0.0,), frozenset({"quay", "hill"}), frozenset({"/made-up/a.mov", "/made-up/b.mov"}))
                  for _ in range(6)]
        found = within("quay", 12) + within("market", 12) + within("hill", 12) + across
        fitted_to = []

        def fitted(kept, penalty):
            fitted_to.append(kept)
            return [1.0], [1.0]

        with mock.patch.object(ranker, "_fitted", side_effect=fitted):
            folds, accuracy, tested = ranker._held_out(found, lambda example: example.shoots, 0.01)
        for fold, kept in zip(folds, fitted_to, strict=True):
            self.assertFalse(any(fold.held_out in example.shoots for example in kept), fold.held_out)
        self.assertEqual([len(kept) for kept in fitted_to], [24, 30, 24])
        # A choice across two shoots is tested with each held out, and counted once.
        self.assertEqual([fold.pairs for fold in folds], [18, 12, 18])
        self.assertEqual((accuracy, tested), (1.0, 42))

    def test_one_shoot_alone_cannot_be_held_out(self):
        self.assertEqual(ranker._held_out(within("quay", 40), lambda example: example.shoots, 0.01), ([], None, 0))

    def test_many_shoots_are_held_out_several_at_a_time(self):
        found = [example for at in range(25) for example in within(f"shoot-{at:02}", 2)]
        folds, accuracy, tested = ranker._held_out(found, lambda example: example.shoots, 0.01)
        self.assertEqual(len(folds), ranker.ROUNDS)
        self.assertEqual(sum(fold.pairs for fold in folds), 50)
        self.assertEqual((folds[0].held_out, accuracy, tested), ("shoot-00 and 2 more", 1.0, 50))


SHOOTS = ("quay", "market", "hill")


class Preferences(test_edit.Interview):
    """Three made-up shoots of six shots each, and an editor who always chose the sharper of two."""

    def setUp(self):
        super().setUp()
        dice = random.Random(11)
        self.sharpness = {}
        self.file = {}
        self.shoots = {}
        for shoot in SHOOTS:
            self.shoots[shoot] = self.filmed(f"{shoot}.mov", f"made-up-{shoot}", dice)
        for made in self.shoots.values():
            self.choose(made)

    def filmed(self, name, project, dice, count=6):
        asset_id = self.asset(name, medium="video", project=project)
        made = []
        for index in range(count):
            sharp = round(dice.uniform(40.0, 260.0), 1)
            shot_id = self.shot(asset_id, index, 10.0 * index, 10.0 * index + dice.uniform(3.0, 9.0), sharpness=sharp)
            self.sharpness[str(shot_id)] = sharp
            self.file[str(shot_id)] = name
            made.append(shot_id)
        return made

    def choose(self, made):
        for one, other in itertools.combinations(made, 2):
            chosen, rejected = sorted((one, other), key=lambda shot_id: -self.sharpness[str(shot_id)])
            decisions.prefer(str(chosen), str(rejected), actor="made-up-editor")

    def preferred(self, name="made-up-taste"):
        return {str(shot_id): value for shot_id, value in self.query(
            "SELECT subject_id, value FROM media_video.scores WHERE name = 'preferred' AND producer = %s",
            (ranker.PRODUCER + name,))}


class TrainingTest(Preferences):
    def test_a_model_learns_what_was_chosen_and_is_tested_on_shoots_held_out(self):
        made = ranker.train("made-up-taste")
        self.assertEqual((made.pairs, made.fitted, made.accuracy, made.tested, made.held_out_by),
                         (45, 1.0, 1.0, 45, "shoot"))
        self.assertEqual([(fold.held_out, fold.pairs) for fold in made.folds],
                         [("made-up-hill", 15), ("made-up-market", 15), ("made-up-quay", 15)])
        self.assertTrue(made.proven)
        self.assertEqual(made.features, ranker.FEATURES)
        feature, weight = made.weights[0]
        self.assertEqual(feature, "sharpness")
        self.assertGreater(weight, 1.0)

    def test_a_pair_ruled_on_twice_counts_as_what_was_said_last(self):
        least, most = sorted(self.shoots["quay"], key=lambda shot_id: self.sharpness[str(shot_id)])[::5]
        self.assertTrue(decisions.prefer(str(least), str(most), actor="made-up-editor", why="the light"))
        found = ranker.examples()
        self.assertEqual(len(found), 45)
        at = ranker.FEATURES.index("sharpness")
        self.assertEqual([(example.won[at], example.lost[at]) for example in found if example.won[at] < example.lost[at]],
                         [(self.sharpness[str(least)], self.sharpness[str(most)])])

    def test_choices_are_kept_to_one_person_one_source_or_some_files(self):
        decisions.prefer(str(self.shoots["quay"][0]), str(self.shoots["hill"][0]), actor="made-up-visitor")
        decisions.prefer(str(self.shoots["quay"][1]), str(self.shoots["hill"][1]), actor="made-up-rule", source="baseline")
        self.assertEqual(len(ranker.examples()), 46)
        self.assertEqual(len(ranker.examples(source=None)), 47)
        self.assertEqual(len(ranker.examples(actor="made-up-visitor")), 1)
        self.assertEqual(len(ranker.examples(source="baseline")), 1)
        # A choice is kept only when both of its shots match.
        self.assertEqual(len(ranker.examples(match="quay")), 15)
        self.assertEqual(ranker.examples(actor="made-up-visitor")[0].shoots, {"made-up-quay", "made-up-hill"})

    def test_too_few_choices_are_not_fitted_to(self):
        with self.assertRaisesRegex(EditError, "45 choices between shots are on record, and a model needs 100"):
            ranker.train("made-up-taste", min_pairs=100)
        with self.assertRaisesRegex(EditError, "15 choices between shots are on record matching 'quay'"):
            ranker.train("made-up-taste", match="quay")

    def test_one_shoot_of_one_file_cannot_be_tested_and_is_never_used(self):
        made = ranker.train("made-up-quay", match="quay", min_pairs=10)
        self.assertEqual((made.pairs, made.accuracy, made.tested, made.held_out_by, made.folds),
                         (15, None, 0, None, ()))
        self.assertFalse(made.proven)
        ranker.save(made)
        self.assertEqual(ranker.apply(made), 0)
        self.assertEqual(self.preferred("made-up-quay"), {})

    def test_one_shoot_of_several_files_holds_out_files(self):
        dice = random.Random(5)
        for name in ("pier-1.mov", "pier-2.mov", "pier-3.mov"):
            self.choose(self.filmed(name, "made-up-pier", dice, count=5))
        made = ranker.train("made-up-pier", match="pier")
        self.assertEqual((made.pairs, made.tested, made.held_out_by, made.accuracy), (30, 30, "file", 1.0))
        self.assertEqual([fold.held_out for fold in made.folds],
                         ["/made-up/pier-1.mov", "/made-up/pier-2.mov", "/made-up/pier-3.mov"])


class ModelsTest(Preferences):
    def test_a_model_is_stored_and_read_back_with_how_it_did(self):
        made = ranker.train("made-up-taste", scope="harbour footage", actor="made-up-editor")
        ranker.save(made)
        self.assertEqual(ranker.load("made-up-taste"), made)
        self.assertEqual(ranker.listing(), [made])
        again = dataclasses.replace(made, scope="anything by the sea")
        ranker.save(again)
        self.assertEqual(ranker.listing(), [again])
        with self.assertRaisesRegex(EditError, "no model is called 'made-up-other'"):
            ranker.load("made-up-other")

    def test_every_shot_gets_a_score_for_how_likely_it_is_to_be_chosen(self):
        made = ranker.train("made-up-taste")
        ranker.save(made)
        self.assertEqual(ranker.apply(made), 18)
        found = self.preferred()
        self.assertEqual(set(found), set(self.sharpness))
        by_sharpness = sorted(found, key=self.sharpness.get)
        self.assertEqual(sorted(found, key=found.get), by_sharpness)
        self.assertLess(found[by_sharpness[0]], 0.2)
        self.assertGreater(found[by_sharpness[-1]], 0.8)
        # The middle shot of them all is as likely as not to be chosen over itself.
        self.assertAlmostEqual((found[by_sharpness[8]] + found[by_sharpness[9]]) / 2, 0.5, places=1)
        self.assertEqual(self.query("SELECT DISTINCT tier, modality FROM media_video.scores WHERE name = 'preferred'"),
                         [(scores.JUDGED, "cross")])
        # Doing it again replaces what was written.
        self.assertEqual(ranker.apply(made), 18)
        self.assertEqual(len(self.preferred()), 18)

    def test_a_model_for_some_files_scores_only_those(self):
        made = dataclasses.replace(ranker.train("made-up-taste"), match="quay|hill")
        self.assertEqual(ranker.apply(made), 12)
        self.assertEqual(set(self.preferred()), {str(shot_id) for shoot in ("quay", "hill") for shot_id in self.shoots[shoot]})

    def test_a_model_that_stops_being_proven_stops_being_used(self):
        made = ranker.train("made-up-taste")
        ranker.save(made)
        ranker.apply(made)
        worse = dataclasses.replace(made, accuracy=0.5)
        ranker.save(worse)
        self.assertEqual(ranker.refresh(), {})
        self.assertEqual(self.preferred(), {})
        ranker.save(made)
        self.assertEqual(ranker.refresh(), {"preferred": 18})

    def test_a_model_is_forgotten_with_what_it_wrote(self):
        made = ranker.train("made-up-taste")
        ranker.save(made)
        ranker.apply(made)
        ranker.forget("made-up-taste")
        self.assertEqual((ranker.listing(), self.preferred()), ([], {}))
        with self.assertRaisesRegex(EditError, "no model is called"):
            ranker.forget("made-up-taste")

    def test_a_recipe_that_weighs_what_is_preferred_ranks_by_it(self):
        made = ranker.train("made-up-taste")
        ranker.apply(made)
        recipe = scores.define("made-up-reel", {"preferred": 1.0}, description="what the editor would choose")
        self.assertEqual(scores.recipe("made-up-reel"), recipe)
        best = [str(entry.subject_id) for entry in scores.ranked("made-up-reel", limit=3).entries]
        self.assertEqual(best, sorted(self.sharpness, key=self.sharpness.get, reverse=True)[:3])


class RecipesTest(test_edit.Interview):
    def test_a_recipe_is_made_and_changed(self):
        scores.define("made-up-reel", {"sharpness": 2.0, "preferred": 1.0})
        self.assertEqual(scores.recipe("made-up-reel"),
                         scores.Recipe("made-up-reel", "", "shot", {"sharpness": 2.0, "preferred": 1.0}, 1))
        scores.define("made-up-reel", {"coherence": 1.0}, subject="passage", description="the clearest", min_modalities=2)
        self.assertEqual(scores.recipe("made-up-reel"),
                         scores.Recipe("made-up-reel", "the clearest", "passage", {"coherence": 1.0}, 2))

    def test_a_recipe_has_to_make_sense(self):
        for weights, changes in (({}, {}), ({"sharpness": 0.0}, {}), ({"sharpness": -1.0}, {}),
                                 ({"sharpness": 1.0}, {"subject": "song"}),
                                 ({"sharpness": 1.0}, {"min_modalities": 0})):
            with self.assertRaises(EditError):
                scores.define("made-up-reel", weights, **changes)
        self.assertNotIn("made-up-reel", [recipe.name for recipe in scores.recipes()])


class RankerCommandsTest(Preferences):
    def setUp(self):
        super().setUp()
        environment = mock.patch.dict(os.environ, {
            "CONSTRUCTIVE_MEDIA_HOME": "/made-up/home",
            "CONSTRUCTIVE_MEDIA_DATABASE_URL": os.environ[TEST_DATABASE_VARIABLE],
        })
        environment.start()
        self.addCleanup(environment.stop)

    def test_a_model_is_fitted_stored_shown_and_forgotten(self):
        status, out, _ = run("fit")
        self.assertEqual(status, 0)
        lines = out.splitlines()
        self.assertEqual(lines[:6], [
            "45 choices",
            "  right about 100% of 45 choices over footage held out by shoot; a coin gets 50%",
            "  right about 100% of the 45 it was fitted to, which it had seen",
            "    100% of   15  without made-up-hill",
            "    100% of   15  without made-up-market",
            "    100% of   15  without made-up-quay",
        ])
        self.assertEqual(lines[6], "  goes by, strongest first (+ more is preferred, - less is):")
        self.assertRegex(lines[7], r"^    \+\d+\.\d\d  sharpness$")
        self.assertEqual((len(lines), lines[-1]), (8 + len(ranker.FEATURES), "nothing stored; --save NAME keeps it"))
        self.assertEqual(run("models")[1], "0 models; `media fit --save NAME` makes one\n")

        status, out, _ = run("fit", "--save", "made-up-taste", "--scope", "harbour footage")
        self.assertEqual((status, out.splitlines()[-1]),
                         (0, "stored as made-up-taste; 18 shots given a `preferred` score"))
        self.assertEqual(run("models")[1], (
            "  made-up-taste  in use  right about 100% of 45 choices over footage held out by shoot; a coin gets 50%\n"
            "1 models\n"))
        status, out, _ = run("models", "made-up-taste")
        self.assertEqual((status, out.splitlines()[0], out.splitlines()[-1]), (0, "made-up-taste: harbour footage", "  in use"))
        # New footage is scored by the models there are when everything else is.
        self.shot(self.camera, 0, 0.0, 20.0, sharpness=100.0)
        self.assertIn("     19  preferred\n", run("score")[1])
        self.assertEqual(run("models", "made-up-taste", "--forget")[1], "forgot made-up-taste\n")
        self.assertEqual(self.preferred(), {})

    def test_a_model_that_cannot_be_tested_is_stored_and_not_used(self):
        status, out, _ = run("fit", "--match", "quay", "--min-pairs", "10", "--save", "made-up-quay")
        self.assertEqual(status, 0)
        self.assertIn("  could not be tested: no footage could be held out and leave enough to fit to\n", out)
        self.assertEqual(out.splitlines()[-1], "stored as made-up-quay, and not used: it has not shown it does better "
                                               "than a coin by more than chance. More choices may change that")
        self.assertEqual(run("models", "made-up-quay")[1].splitlines()[-1],
                         "  not in use: not shown to be better than a coin")

    def test_what_cannot_be_done_is_said(self):
        self.assertEqual(run("fit", "--min-pairs", "500")[::2], (
            1, "media: 45 choices between shots are on record, and a model needs 500. "
               "`media pairs` lists pairs worth choosing between\n"))
        self.assertEqual(run("models", "made-up-other")[::2],
                         (1, "media: no model is called 'made-up-other'; `media models` lists them\n"))
        self.assertEqual(run("models", "--forget")[::2], (1, "media: name the model to forget\n"))

    def test_a_recipe_is_shown_made_and_changed(self):
        self.assertEqual(run("recipe", "broll")[1].splitlines()[1],
                         "  sharpness 0.34, safe over audio 0.28, colour variety 0.24, energy lift 0.14")
        status, out, _ = run("recipe", "made-up-reel", "--like", "broll", "--weigh", "preferred=0.5",
                             "--weigh", "energy_lift=0", "--describe", "what the editor would choose")
        self.assertEqual((status, out), (0, (
            "made-up-reel: ranks shots. what the editor would choose\n"
            "  preferred 0.5, sharpness 0.34, safe over audio 0.28, colour variety 0.24\n"
            "no shot has a score called colour_variety, preferred, safe_over_audio, sharpness yet, "
            "so for now it counts for nothing\n")))
        run("fit", "--save", "made-up-taste")
        status, out, _ = run("recipe", "made-up-reel", "--weigh", "preferred=1", "--weigh", "sharpness=0",
                             "--weigh", "colour variety=0", "--weigh", "safe_over_audio=0")
        self.assertEqual((status, out), (0, "made-up-reel: ranks shots. what the editor would choose\n  preferred 1\n"))
        best = run("rank", "made-up-reel", "--top", "1")[1].splitlines()[0]
        self.assertRegex(best, rf"^  \d\.\d\d  {self.file[max(self.sharpness, key=self.sharpness.get)]}  ")
        self.assertEqual(run("recipe", "made-up-other")[::2],
                         (1, "media: no recipe is called 'made-up-other'; `media rank` lists them\n"))
        self.assertEqual(run("recipe", "made-up-reel", "--weigh", "sharper")[::2],
                         (1, "media: --weigh takes SCORE=WEIGHT, as in preferred=0.5, not 'sharper'\n"))
        self.assertEqual(run("recipe", "made-up-none", "--describe", "nothing")[::2],
                         (1, "media: a recipe needs at least one score to weigh, each by more than nothing\n"))
