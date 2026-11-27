"""Finding passages by what they mean, with a stand-in for the model that says what that is."""

import hashlib
import os
from unittest import mock

from constructive_media import takes
from constructive_media.edit import EditError, flags, passages, search, timelines
from constructive_media.edit.said import tokens

from .support import TEST_DATABASE_VARIABLE
from .test_assemble import Talks
from .test_cli import run


class Bag:
    """A stand-in embedder: each longer word counts towards one number, so texts that share words are near."""

    name = "made-up"

    def __init__(self, model="made-up-bag", size=search.DIMENSIONS):
        self.model, self.size, self.sent = model, size, []

    def embed(self, texts, *, asking=False):
        self.sent.append((list(texts), asking))
        return [self.numbers(text) for text in texts]

    def numbers(self, text):
        numbers = [0.0] * self.size
        for word in tokens(text):
            if len(word) > 3:
                numbers[int(hashlib.sha256(word.encode()).hexdigest(), 16) % self.size] += 1.0
        if not any(numbers):
            numbers[0] = 1.0
        return numbers


class Indexed(Talks):
    """Both made-up interviews, with every passage that may be sent to a model indexed."""

    def setUp(self):
        super().setUp()
        self.wall()
        self.bag = Bag()
        self.done = search.index(by=self.bag)

    def kept(self):
        return {passage_id: (model, mark) for passage_id, model, mark in self.query(
            "SELECT passage_id, model, text_hash FROM media_video.passage_embeddings")}

    def said(self, passage_id):
        return self.query("SELECT text FROM media_video.passages WHERE id = %s", (passage_id,))[0][0]

    def found(self, question, **options):
        return search.search(question, by=self.bag, **options)

    def embed(self, passage_id, model="made-up-bag"):
        """Index a passage by hand, as it would have been before anyone had it held back."""
        said = self.said(passage_id)
        self.query("INSERT INTO media_video.passage_embeddings (passage_id, embedding, model, text_hash) "
                   "VALUES (%s, %s::vector, %s, %s) RETURNING passage_id",
                   (passage_id, str(self.bag.numbers(said)), model, search.mark(said)))


class IndexTest(Indexed):
    def test_every_passage_is_kept_with_a_mark_of_its_words(self):
        self.assertEqual(self.done, search.Indexed(3, 3, "made-up-bag"))
        self.assertEqual(self.kept(), {passage_id: ("made-up-bag", search.mark(self.said(passage_id)))
                                         for passage_id in (self.first, self.built, self.fish)})
        ((sent, asking),) = self.bag.sent
        self.assertFalse(asking)
        self.assertEqual(sorted(sent), sorted(" ".join(self.said(passage_id).split())
                                              for passage_id in (self.first, self.built, self.fish)))

    def test_what_someone_asked_not_to_be_used_is_never_sent(self):
        self.assertIn("Off the record", self.said(self.second))
        self.assertFalse(any("record" in text for texts, _ in self.bag.sent for text in texts))
        # Not even when nobody has scanned for it since the words were transcribed.
        self.query("DELETE FROM media_video.flags RETURNING id")
        self.assertEqual(flags.withheld([self.second]), set())
        self.assertEqual(search.index(by=self.bag).considered, 0)
        # A person who dismisses the flag has said it may be used.
        flags.scan()
        self.query("UPDATE media_video.flags SET accepted = false WHERE kind = 'off_record' RETURNING id")
        self.assertEqual(search.index(by=self.bag), search.Indexed(1, 1, "made-up-bag"))
        self.assertIn(self.second, self.kept())

    def test_only_words_that_have_changed_are_sent_again(self):
        self.assertEqual(search.index(by=self.bag), search.Indexed(0, 0, "made-up-bag"))
        self.assertEqual(len(self.bag.sent), 1)
        corrected = self.said(self.fish).replace("herring", "mackerel") + " It's a \\ hard trade."
        self.query("UPDATE media_video.passages SET text = %s WHERE id = %s RETURNING id", (corrected, self.fish))
        self.assertEqual(search.waiting("made-up-bag"), [(self.fish, corrected)])
        self.assertEqual(search.index(by=self.bag), search.Indexed(1, 1, "made-up-bag"))
        self.assertEqual(self.bag.sent[-1][0], [corrected])
        self.assertEqual(self.kept()[self.fish], ("made-up-bag", search.mark(corrected)))
        # The mark the database works out is the one worked out here, whatever is in the words.
        self.assertEqual(search.waiting("made-up-bag"), [])

    def test_another_model_starts_again_and_a_scope_is_kept_to(self):
        other = Bag("another-bag")
        self.assertEqual(search.index(take="made-up-harbour-wall", by=other), search.Indexed(2, 2, "another-bag"))
        self.assertEqual({model for model, _ in self.kept().values()}, {"made-up-bag", "another-bag"})
        self.assertEqual(search.index("no-such-project", by=other).considered, 0)
        self.assertEqual(search.index(by=other, limit=0).considered, 0)
        self.assertEqual(search.index(by=other, batch=1), search.Indexed(1, 1, "another-bag"))

    def test_it_is_sent_a_few_at_a_time_and_says_how_far_it_has_got(self):
        self.query("DELETE FROM media_video.passage_embeddings RETURNING passage_id")
        bag, seen = Bag(), []
        search.index(by=bag, batch=2, progress=lambda done, of: seen.append((done, of)))
        self.assertEqual([len(texts) for texts, _ in bag.sent], [2, 1])
        self.assertEqual(seen, [(2, 3), (3, 3)])

    def test_a_model_of_another_size_is_refused_before_anything_of_its_is_kept(self):
        with self.assertRaises(EditError) as raised:
            search.index(by=Bag("small-bag", size=8))
        self.assertEqual(str(raised.exception), "small-bag gives 8 numbers for a text and the library keeps 1024; "
                                                "choose an embedding model that gives 1024")
        self.assertEqual({model for model, _ in self.kept().values()}, {"made-up-bag"})

        class Short(Bag):
            def embed(self, texts, *, asking=False):
                return super().embed(texts[:1])

        with self.assertRaises(EditError) as raised:
            search.index(by=Short("short-bag"))
        self.assertEqual(str(raised.exception), "short-bag was given 3 passages and answered for 1")
        with self.assertRaises(EditError):
            search.search("the lamp", by=Bag("small-bag", size=8))


class SearchTest(Indexed):
    def test_what_is_nearest_the_question_comes_first(self):
        found = self.found("Who sold herring from a barrow every morning?")
        self.assertEqual(self.bag.sent[-1], (["Who sold herring from a barrow every morning?"], True))
        self.assertEqual((found.model, found.withheld, found.unindexed), ("made-up-bag", 0, 0))
        self.assertEqual([hit.passage_id for hit in found.hits][0], self.fish)
        self.assertEqual({hit.passage_id for hit in found.hits}, {self.first, self.built, self.fish})
        best = found.hits[0]
        self.assertEqual((best.take, best.take_id is not None, best.answers, best.why),
                         ("made-up-harbour-wall", True, None, ""))
        self.assertTrue(best.text.startswith("My mother sold fish from a barrow"))
        self.assertAlmostEqual(best.duration_s, best.end_s - best.start_s)
        self.assertGreater(best.similarity, 0.4)
        self.assertGreater(best.similarity, found.hits[1].similarity + 0.2)
        self.assertAlmostEqual(best.score, 0.65 * best.similarity + 0.35 * best.quality, places=3)
        self.assertEqual([hit.passage_id for hit in self.found("the great storm and the lamp going dark").hits][0],
                         self.first)
        self.assertEqual([hit.passage_id for hit in self.found("stone carried from the quarry").hits][0], self.built)

    def test_how_much_being_on_the_subject_counts_is_a_choice(self):
        question = "Who sold herring from a barrow every morning?"
        near = self.found(question, relevance=1.0)
        self.assertEqual([hit.score for hit in near.hits], [hit.similarity for hit in near.hits])
        self.assertEqual(near.hits[0].passage_id, self.fish)
        good = self.found(question, relevance=0.0)
        self.assertEqual([hit.score for hit in good.hits], [hit.quality for hit in good.hits])
        self.assertEqual([hit.score for hit in good.hits], sorted((hit.quality for hit in good.hits), reverse=True))
        # How good it is, is what the recipe makes of it.
        self.query("UPDATE media_video.scores SET value = 0 WHERE subject_id = %s RETURNING id", (self.fish,))
        self.assertEqual(self.found(question, relevance=0.0).hits[-1].passage_id, self.fish)
        self.assertEqual(self.found(question, relevance=0.0).hits[-1].quality, 0.0)
        for odd in (-0.1, 1.5):
            with self.assertRaises(EditError):
                self.found(question, relevance=odd)
        with self.assertRaises(EditError):
            self.found("   ")

    def test_a_search_is_kept_to_what_was_asked_for(self):
        question = "Who sold herring from a barrow every morning?"
        self.assertEqual([hit.passage_id for hit in self.found(question, take="made-up-lighthouse").hits], [self.first])
        self.assertEqual(self.found(question, project="no-such-project").hits, [])
        self.assertEqual([hit.passage_id for hit in self.found(question, top=1).hits], [self.fish])
        fish = self.found(question).hits[0].duration_s
        self.assertNotIn(self.fish, [hit.passage_id for hit in self.found(question, min_len_s=fish + 1).hits])
        self.assertEqual([hit.passage_id for hit in self.found(question, max_len_s=fish).hits], [self.fish])
        self.assertEqual(self.found(question, min_coherence=1.1).hits, [])

    def test_what_was_cut_for_good_is_not_found_unless_asked_for(self):
        question = "Who sold herring from a barrow every morning?"
        self.query("INSERT INTO media_video.exclusions (passage_id, reason) VALUES (%s, 'made-up') RETURNING id",
                   (self.fish,))
        self.assertNotIn(self.fish, [hit.passage_id for hit in self.found(question).hits])
        self.assertEqual(self.found(question, include_cut=True).hits[0].passage_id, self.fish)

    def test_what_someone_asked_not_to_be_used_is_never_found(self):
        self.embed(self.second)
        found = self.found("the harbour master asleep at his post")
        self.assertEqual(found.withheld, 1)
        self.assertNotIn(self.second, [hit.passage_id for hit in found.hits])

    def test_words_that_have_changed_and_another_models_numbers_are_not_searched(self):
        question = "Who sold herring from a barrow every morning?"
        self.query("UPDATE media_video.passages SET text = text || ' Every morning.' WHERE id = %s RETURNING id",
                   (self.fish,))
        found = self.found(question)
        self.assertNotIn(self.fish, [hit.passage_id for hit in found.hits])
        self.assertEqual(found.unindexed, 1)
        other = search.search(question, by=Bag("another-bag"))
        self.assertEqual((other.hits, other.unindexed, other.model), ([], 3, "another-bag"))

    def test_a_moment_is_found_once_however_many_passages_cover_it(self):
        passages.propose(takes.find("made-up-harbour-wall"), by="sentence")
        search.index(by=self.bag)
        self.assertGreater(len(self.kept()), 6)
        hits = self.found("Who sold herring from a barrow every morning?", top=50).hits
        self.assertEqual(hits[0].take, "made-up-harbour-wall")
        for at, hit in enumerate(hits):
            for other in hits[:at]:
                if other.take_id == hit.take_id:
                    shared = min(hit.end_s, other.end_s) - max(hit.start_s, other.start_s)
                    self.assertLessEqual(shared, 0.3 * min(hit.duration_s, other.duration_s))


class CutTest(Indexed):
    def test_a_question_is_answered_with_a_cut(self):
        made = search.cut("the harbour wall and the herring and the great storm", "made-up-found", by=self.bag)
        self.assertEqual([part.passage_id for part in made.parts], [self.built, self.fish, self.first])
        cut = timelines.load("made-up-found")
        self.assertEqual(cut, made.timeline)
        self.assertEqual((cut.title, len(cut.clips)), ("the harbour wall and the herring and the great storm", 3))
        self.assertEqual([clip.picture for clip in cut.clips], [self.quay, self.quay, self.camera])
        # The lighthouse is heard through the collar, as in any other assembly.
        self.assertEqual(cut.clips[-1].sound, self.collar)

    def test_the_best_can_come_first_and_the_cut_is_no_longer_than_asked(self):
        question = "Who sold herring from a barrow every morning?"
        made = search.cut(question, "made-up-found", by=self.bag, chronological=False, title="Fish")
        self.assertEqual(made.parts[0].passage_id, self.fish)
        self.assertEqual(made.timeline.title, "Fish")
        fish = made.parts[0].end_s - made.parts[0].start_s
        short = search.cut(question, "made-up-short", by=self.bag, target_s=fish + 1)
        self.assertEqual([part.passage_id for part in short.parts], [self.fish])
        with self.assertRaises(EditError) as raised:
            search.cut(question, "made-up-none", by=self.bag, target_s=2)
        self.assertEqual(str(raised.exception), "everything that answers to that is longer than 2 seconds")
        with self.assertRaises(EditError) as raised:
            search.cut(question, "made-up-none", by=Bag("another-bag"))
        self.assertEqual(str(raised.exception),
                         "nothing that is indexed answers to that; 3 passages are not indexed yet")


class SearchCommandsTest(Indexed):
    def setUp(self):
        super().setUp()
        for patch in (
            mock.patch.dict(os.environ, {"CONSTRUCTIVE_MEDIA_HOME": "/made-up/home",
                                         "CONSTRUCTIVE_MEDIA_DATABASE_URL": os.environ[TEST_DATABASE_VARIABLE]}),
            mock.patch("constructive_media.text.embedder", return_value=self.bag),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def test_index_says_what_it_took_in(self):
        self.assertEqual(run("index"), (0, "nothing to index: made-up-bag has every passage as it now reads\n", ""))
        self.query("DELETE FROM media_video.passage_embeddings RETURNING passage_id")
        self.assertEqual(run("index", "--take", "made-up-harbour-wall"), (0, "2 passages indexed by made-up-bag\n", ""))
        self.assertEqual(run("index", "--project", "made-up"), (0, "1 passages indexed by made-up-bag\n", ""))

    def test_search_lists_what_it_found(self):
        self.embed(self.second)
        self.query("UPDATE media_video.passages SET text = text || ' Every one.' WHERE id = %s RETURNING id",
                   (self.built,))
        status, out, err = run("search", "Who sold herring from a barrow every morning?", "--top", "5")
        self.assertEqual((status, err), (0, ""))
        fish, light, count, held, waiting = out.splitlines()
        self.assertRegex(fish, r"^  0\.\d\d  made-up-harbour-wall  0:40\.0-0:5\d\.\d  near 0\.\d\d  good 0\.\d\d  "
                               r"My mother sold fish from a barrow at the top of the slip every morning\.…$")
        self.assertIn("  made-up-lighthouse  0:10.0-0:29.9  ", light)
        self.assertEqual(count, "2 passages found by made-up-bag")
        self.assertEqual(held, "1 left out: someone in them asks for it not to be used")
        self.assertEqual(waiting, "1 passages are not indexed as they now read; `media index` takes them in")

    def test_search_can_store_what_it_found_as_a_cut(self):
        status, out, err = run("search", "Who sold herring from a barrow every morning?", "--cut", "made-up-found",
                               "--seconds", "20", "--best-first", "--take", "made-up-harbour-wall")
        self.assertEqual((status, err), (0, ""))
        clip, count = out.splitlines()
        self.assertRegex(clip, r"^    1  made-up-harbour-wall  0:40\.0-0:5\d\.\d  \(1\d\.\ds\)  My mother sold fish")
        self.assertRegex(count, r"^1 clips, 0:1\d\.\d, stored as made-up-found$")
        self.assertEqual(len(timelines.load("made-up-found").clips), 1)
        self.assertEqual(run("search", "anything at all", "--relevance", "2")[::2],
                         (1, "media: relevance is a share, from 0 to 1\n"))
