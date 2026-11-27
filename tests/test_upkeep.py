"""Keeping transcripts right: what is mended, what is put back, and when a second listen replaces the first.

The recogniser here is a stand-in that says what it is told to, as surely as
it is told to be.
"""

import tempfile
import unittest
from pathlib import Path

from constructive_media.audio import transcripts, upkeep
from constructive_media.transcription import Segment, TranscriptionError, Word

from .support import DatabaseTest


class Listener:
    name = "fake"

    def __init__(self, *lines, sure=-0.2, model="fake-1"):
        self.lines, self.sure, self.model = lines, sure, model
        self.asked = []

    def transcribe(self, audio, *, language="en", prompt=None):
        self.asked.append((audio.name, language, prompt))
        heard = []
        for index, line in enumerate(self.lines):
            start = index * 10.0
            words = tuple(Word(start + n * 0.5, start + n * 0.5 + 0.4, word, 0.8)
                          for n, word in enumerate(line.split()))
            heard.append(Segment(start, words[-1].end, line, words, avg_logprob=self.sure))
        return heard


class UpkeepTest(DatabaseTest):
    def asset(self, name, project=None, seconds=120.0):
        ((asset_id,),) = self.query(
            """INSERT INTO media.assets (medium, path, content_hash, has_audio, project, duration_s)
               VALUES ('audio', %s, %s, true, %s, %s) RETURNING id""",
            (f"/made-up/{name}", f"made-up-{name}", project, seconds),
        )
        return asset_id

    def heard(self, name, *lines, project=None, seconds=120.0, sure=-0.2, model="fake-1"):
        asset_id = self.asset(name, project, seconds)
        transcripts.transcribe(rf"^/made-up/{name}$", transcriber=Listener(*lines, sure=sure, model=model))
        return asset_id

    def lines(self, asset_id):
        return self.query(
            "SELECT text, text_asr FROM media_video.segments WHERE asset_id = %s ORDER BY idx", (asset_id,))

    def words(self, asset_id):
        return self.query(
            """SELECT w.word, w.word_asr FROM media_video.words w
                 JOIN media_video.segments s ON s.id = w.segment_id
                WHERE s.asset_id = %s ORDER BY s.idx, w.idx""",
            (asset_id,),
        )

    def corrections(self, asset_id):
        return self.query(
            """SELECT c.before_text, c.after_text, c.rule, c.actor FROM media_video.corrections c
                 JOIN media_video.segments s ON s.id = c.segment_id
                WHERE s.asset_id = %s ORDER BY c.applied_at""",
            (asset_id,),
        )


class MishearingTest(UpkeepTest):
    def test_a_mishearing_is_recorded_once_and_adds_a_term_that_is_new(self):
        self.assertEqual(upkeep.mishear("Zentrova", ["zen trova", "Sentrova"], "made-up-harbour"), 2)
        self.assertEqual(upkeep.mishear("Zentrova", ["sentrova", " zen   trova ", "Zentrover", ""],
                                        "made-up-harbour"), 1)
        self.assertEqual(upkeep.known("made-up-harbour")["Zentrova"], ["zen trova", "Sentrova", "Zentrover"])
        self.assertIn("Zentrova", transcripts.terms("made-up-harbour"))

    def test_a_term_with_nothing_misheard_is_not_listed(self):
        transcripts.learn(["Ottoline Varga"], "made-up-harbour")
        self.assertNotIn("Ottoline Varga", upkeep.known("made-up-harbour"))

    def test_a_term_is_not_misheard_as_itself_or_as_part_of_itself(self):
        with self.assertRaisesRegex(upkeep.UpkeepError, "as itself"):
            upkeep.mishear("Zentrova", ["Zentrova"])
        with self.assertRaisesRegex(upkeep.UpkeepError, "is part of"):
            upkeep.mishear("Ottoline Varga", ["varga"])
        with self.assertRaisesRegex(upkeep.UpkeepError, "none was given"):
            upkeep.mishear(" ", ["anything"])
        self.assertEqual(self.query("SELECT 1 FROM media_video.vocabulary WHERE term = 'Ottoline Varga'"), [])

    def test_a_term_may_be_misheard_as_itself_in_another_case(self):
        self.assertEqual(upkeep.mishear("ZENTROVA", ["Zentrova"]), 1)
        self.assertEqual(upkeep.Rule("Zentrova", "ZENTROVA").mend("the zentrova's dock"), "the ZENTROVA's dock")


class MendTest(UpkeepTest):
    def test_a_line_and_its_words_are_mended_and_what_was_heard_is_kept(self):
        asset = self.heard("harbour.wav", "We met sentrova at dawn.", "Nobody saw us leave.")
        upkeep.mishear("Zentrova", ["sentrova"])
        mended = upkeep.mend(match=r"^/made-up/")
        self.assertEqual(mended, [upkeep.Mended("harbour.wav", 0.0, "We met sentrova at dawn.",
                                                "We met Zentrova at dawn.", ("sentrova -> Zentrova",))])
        self.assertEqual(self.lines(asset), [("We met Zentrova at dawn.", "We met sentrova at dawn."),
                                             ("Nobody saw us leave.", "Nobody saw us leave.")])
        self.assertEqual(self.words(asset)[:3], [("We", None), ("met", None), ("Zentrova", "sentrova")])
        self.assertEqual(self.corrections(asset), [
            ("We met sentrova at dawn.", "We met Zentrova at dawn.", "sentrova -> Zentrova", "mend")])

    def test_a_plural_and_a_possessive_are_kept_and_only_whole_words_are_mended(self):
        asset = self.heard("harbour.wav", "Sentrova's boats and two Sentrovas, not sentrovan ones.")
        upkeep.mishear("Zentrova", ["sentrova"])
        upkeep.mend(match=r"^/made-up/")
        self.assertEqual(self.lines(asset)[0][0], "Zentrova's boats and two Zentrovas, not sentrovan ones.")
        self.assertEqual([word for word, _ in self.words(asset)],
                         ["Zentrova's", "boats", "and", "two", "Zentrovas,", "not", "sentrovan", "ones."])

    def test_a_mishearing_of_several_words_is_mended_in_the_line_and_its_words_are_left(self):
        asset = self.heard("harbour.wav", "Ask zen  trova about the tide.")
        upkeep.mishear("Zentrova", ["zen trova"])
        upkeep.mend(match=r"^/made-up/")
        self.assertEqual(self.lines(asset)[0][0], "Ask Zentrova about the tide.")
        self.assertEqual(self.words(asset)[1:3], [("zen", None), ("trova", None)])

    def test_the_longer_mishearing_is_tried_first(self):
        asset = self.heard("harbour.wav", "Otto lean varga and otto came.")
        upkeep.mishear("Ottoline Varga", ["otto lean varga"])
        upkeep.mishear("Otho", ["otto"])
        (line,) = upkeep.mend(match=r"^/made-up/")
        self.assertEqual(line.after, "Ottoline Varga and Otho came.")
        self.assertEqual(line.rules, ("otto lean varga -> Ottoline Varga", "otto -> Otho"))
        self.assertEqual([(before, after) for before, after, _, _ in self.corrections(asset)], [
            ("Otto lean varga and otto came.", "Ottoline Varga and otto came."),
            ("Ottoline Varga and otto came.", "Ottoline Varga and Otho came."),
        ])

    def test_a_projects_mishearing_is_mended_only_in_its_recordings(self):
        ours = self.heard("ours.wav", "We met sentrova.", project="made-up-harbour")
        theirs = self.heard("theirs.wav", "We met sentrova.", project="made-up-market")
        upkeep.mishear("Zentrova", ["sentrova"], "made-up-harbour")
        self.assertEqual([line.file for line in upkeep.mend(match=r"^/made-up/")], ["ours.wav"])
        self.assertEqual(self.lines(ours)[0][0], "We met Zentrova.")
        self.assertEqual(self.lines(theirs)[0][0], "We met sentrova.")

    def test_only_the_project_or_the_recordings_asked_for_are_mended(self):
        self.heard("ours.wav", "We met sentrova.", project="made-up-harbour")
        self.heard("theirs.wav", "We met sentrova.", project="made-up-market")
        upkeep.mishear("Zentrova", ["sentrova"])
        self.assertEqual([line.file for line in upkeep.mend("made-up-market", r"^/made-up/")], ["theirs.wav"])
        self.assertEqual([line.file for line in upkeep.mend(match=r"^/made-up/ours")], ["ours.wav"])

    def test_with_nothing_kept_the_lines_are_shown_and_nothing_is_written(self):
        asset = self.heard("harbour.wav", "We met sentrova at dawn.")
        upkeep.mishear("Zentrova", ["sentrova"])
        (line,) = upkeep.mend(match=r"^/made-up/", keep=False)
        self.assertEqual(line.after, "We met Zentrova at dawn.")
        self.assertEqual(self.lines(asset)[0][0], "We met sentrova at dawn.")
        self.assertEqual(self.corrections(asset), [])

    def test_mending_twice_does_nothing_more(self):
        asset = self.heard("harbour.wav", "We met sentrova at dawn.")
        upkeep.mishear("Zentrova", ["sentrova"])
        upkeep.mend(match=r"^/made-up/")
        self.assertEqual(upkeep.mend(match=r"^/made-up/"), [])
        self.assertEqual(len(self.corrections(asset)), 1)

    def test_a_second_mending_keeps_what_was_first_heard(self):
        asset = self.heard("harbour.wav", "We met sentrova at dawn.")
        upkeep.mishear("Zentrover", ["sentrova"])
        upkeep.mend(match=r"^/made-up/")
        upkeep.mishear("Zentrova", ["zentrover"])
        upkeep.mend(match=r"^/made-up/")
        self.assertEqual(self.lines(asset), [("We met Zentrova at dawn.", "We met sentrova at dawn.")])
        self.assertEqual(self.words(asset)[2], ("Zentrova", "sentrova"))

    def test_restoring_puts_lines_and_words_back_and_drops_the_corrections(self):
        asset = self.heard("harbour.wav", "We met sentrova at dawn.", "Sentrova's boat left.")
        other = self.heard("market.wav", "We met sentrova.", project="made-up-market")
        upkeep.mishear("Zentrova", ["sentrova"])
        upkeep.mend(match=r"^/made-up/")
        self.assertEqual(upkeep.restore(match=r"^/made-up/harbour"), (2, 2))
        self.assertEqual(self.lines(asset), [("We met sentrova at dawn.",) * 2, ("Sentrova's boat left.",) * 2])
        self.assertEqual([heard for _, heard in self.words(asset)], [None] * 8)
        self.assertEqual(self.words(asset)[2][0], "sentrova")
        self.assertEqual(self.corrections(asset), [])
        self.assertEqual(self.lines(other)[0][0], "We met Zentrova.")
        self.assertEqual(upkeep.restore("made-up-market", r"^/made-up/"), (1, 1))
        self.assertEqual(upkeep.restore(match=r"^/made-up/"), (0, 0))

    def test_with_no_mishearing_recorded_nothing_is_read(self):
        self.heard("harbour.wav", "We met sentrova at dawn.")
        self.assertEqual(upkeep.mend(match=r"^/made-up/"), [])


class RelistenTest(UpkeepTest):
    def weak(self, **how):
        return [(Path(path).name, model, sure, segments)
                for _, path, _, _, model, sure, segments in upkeep.weakest(match=r"^/made-up/", **how)]

    def test_the_unsure_and_the_sparse_are_found_the_least_sure_first(self):
        self.heard("unsure.wav", "a b", "c d", "e f", "g h", sure=-0.9, model="quick-1")
        self.heard("sparse.wav", "a b", sure=-0.1, model="quick-1")
        self.heard("fine.wav", "a b", "c d", "e f", "g h", sure=-0.1, model="quick-1")
        self.heard("short.wav", "a b", sure=-0.9, seconds=30.0, model="quick-1")
        self.assertEqual(self.weak(), [("unsure.wav", "quick-1", -0.9, 4), ("sparse.wav", "quick-1", -0.1, 1)])
        self.assertEqual(self.weak(limit=1), [("unsure.wav", "quick-1", -0.9, 4)])
        self.assertEqual([name for name, *_ in self.weak(unsure=-1.5, sparse=0.1)], [])
        self.assertEqual([name for name, *_ in self.weak(min_length_s=10.0)],
                         ["short.wav", "unsure.wav", "sparse.wav"])

    def test_a_transcript_the_same_model_wrote_is_not_listened_to_again(self):
        self.heard("unsure.wav", "a b", sure=-0.9, model="quick-1")
        self.assertEqual(len(self.weak(model="careful-1")), 1)
        self.assertEqual(self.weak(model="quick-1"), [])
        listener = Listener("a b", "c d", model="quick-1")
        self.assertEqual(upkeep.relisten(match=r"^/made-up/", transcriber=listener), [])
        self.assertEqual(listener.asked, [])

    def test_a_better_transcript_has_to_be_surer_and_to_have_found_no_less(self):
        self.assertTrue(upkeep.better(-0.9, 4, -0.3, 4))
        self.assertTrue(upkeep.better(-0.9, 4, -0.3, 9))
        self.assertFalse(upkeep.better(-0.9, 4, -0.3, 3))
        self.assertFalse(upkeep.better(-0.9, 4, -0.9, 9))
        self.assertFalse(upkeep.better(-0.3, 4, -0.9, 9))
        self.assertTrue(upkeep.better(None, 4, -0.9, 5))
        self.assertFalse(upkeep.better(None, 4, -0.1, 4))
        self.assertTrue(upkeep.better(-0.9, 4, None, 5))
        self.assertFalse(upkeep.better(-0.9, 4, None, 4))

    def test_a_better_second_listen_replaces_the_first_and_a_worse_one_changes_nothing(self):
        better = self.heard("better.wav", "The tied is in.", sure=-0.9, model="quick-1", project="made-up-harbour")
        worse = self.heard("worse.wav", "Nobody saw us leave.", "We sailed.", sure=-0.7, model="quick-1")
        transcripts.learn(["Zentrova"], "made-up-harbour")
        listener = Listener("The tide is in.", sure=-0.3, model="careful-1")
        done = upkeep.relisten(match=r"^/made-up/", language="fr", transcriber=listener)
        self.assertEqual(done, [
            upkeep.Reheard("better.wav", "quick-1", -0.9, 1, -0.3, 1, True),
            upkeep.Reheard("worse.wav", "quick-1", -0.7, 2, -0.3, 1, False),
        ])
        self.assertEqual(listener.asked, [("better.wav", "fr", "Zentrova"), ("worse.wav", "fr", None)])
        self.assertEqual(self.lines(better), [("The tide is in.", "The tide is in.")])
        self.assertEqual(self.lines(worse), [("Nobody saw us leave.",) * 2, ("We sailed.",) * 2])
        models = self.query(
            """SELECT a.path, min(s.model) FROM media_video.segments s JOIN media.assets a ON a.id = s.asset_id
                WHERE a.path ~ '^/made-up/' GROUP BY 1 ORDER BY 1""")
        self.assertEqual(models, [("/made-up/better.wav", "careful-1"), ("/made-up/worse.wav", "quick-1")])

    def test_what_was_mended_is_mended_in_the_new_transcript(self):
        asset = self.heard("harbour.wav", "We met sentrova.", sure=-0.9, model="quick-1")
        upkeep.mishear("Zentrova", ["sentrova"])
        upkeep.mend(match=r"^/made-up/")
        upkeep.relisten(match=r"^/made-up/", vocabulary=False,
                        transcriber=Listener("We met sentrova at dawn.", sure=-0.3, model="careful-1"))
        self.assertEqual(self.lines(asset), [("We met Zentrova at dawn.", "We met sentrova at dawn.")])
        self.assertEqual(len(self.corrections(asset)), 1)

    def test_with_nothing_kept_the_listening_is_done_and_nothing_is_written(self):
        asset = self.heard("harbour.wav", "The tied is in.", sure=-0.9, model="quick-1")
        (heard,) = upkeep.relisten(match=r"^/made-up/", keep=False,
                                   transcriber=Listener("The tide is in.", sure=-0.3, model="careful-1"))
        self.assertTrue(heard.better)
        self.assertEqual(self.lines(asset)[0][0], "The tied is in.")

    def test_how_sure_is_the_mean_over_the_segments_that_say(self):
        said = [Segment(0, 1, "a", (), avg_logprob=-0.2), Segment(1, 2, "b", (), avg_logprob=None),
                Segment(2, 3, "c", (), avg_logprob=-0.6)]
        self.assertAlmostEqual(upkeep.sureness(said), -0.4)
        self.assertIsNone(upkeep.sureness(said[1:2]))
        self.assertIsNone(upkeep.sureness([]))


class TranscribeExtrasTest(UpkeepTest):
    def test_only_the_projects_recordings_are_transcribed(self):
        self.asset("ours.wav", "made-up-harbour")
        self.asset("theirs.wav", "made-up-market")
        done = transcripts.transcribe(r"^/made-up/", project="made-up-market", transcriber=Listener("a b"))
        self.assertEqual(done, [("theirs.wav", 1)])

    def test_another_file_can_be_listened_to_in_place_of_one_recording(self):
        asset = self.asset("harbour.wav")
        listener = Listener("We sailed at dawn.")
        with tempfile.TemporaryDirectory() as folder:
            cleaned = Path(folder) / "harbour-cleaned.wav"
            cleaned.write_bytes(b"made up")
            done = transcripts.transcribe(r"^/made-up/harbour", source=cleaned, transcriber=listener)
            self.assertEqual(done, [("harbour.wav", 1)])
            self.assertEqual(listener.asked, [("harbour-cleaned.wav", "en", None)])
            self.assertEqual(self.lines(asset)[0][0], "We sailed at dawn.")

            self.asset("market.wav")
            self.asset("quay.wav")
            with self.assertRaisesRegex(TranscriptionError, "one recording, and 2 are waiting"):
                transcripts.transcribe(r"^/made-up/", source=cleaned, transcriber=listener)
            with self.assertRaisesRegex(TranscriptionError, "0 are waiting.*--force"):
                transcripts.transcribe(r"^/made-up/harbour", source=cleaned, transcriber=listener)
            with self.assertRaisesRegex(TranscriptionError, "no file at"):
                transcripts.transcribe(r"^/made-up/market", source=Path(folder) / "missing.wav",
                                       transcriber=listener)

    def test_a_speaker_is_written_beside_every_line(self):
        asset = self.asset("lapel.wav")
        transcripts.transcribe(r"^/made-up/lapel", speaker="Ottoline", transcriber=Listener("a b", "c d"))
        self.assertEqual(self.query("SELECT DISTINCT speaker FROM media_video.segments WHERE asset_id = %s",
                                    (asset,)), [("Ottoline",)])
        transcripts.transcribe(r"^/made-up/lapel", force=True, transcriber=Listener("a b"))
        self.assertEqual(self.query("SELECT speaker FROM media_video.segments WHERE asset_id = %s", (asset,)),
                         [(None,)])


if __name__ == "__main__":
    unittest.main()
