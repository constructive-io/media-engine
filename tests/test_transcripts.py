"""Transcripts in the database: what is written, what is skipped, what is replaced.

The recogniser here is a stand-in that says the same few sentences about every
recording and remembers how it was asked.
"""

import unittest
from unittest import mock

from constructive_media.audio import transcripts
from constructive_media.transcription import Segment, Word

from .support import DatabaseTest


def sentence(text, start):
    words = tuple(Word(start + n * 0.5, start + n * 0.5 + 0.4, word, 0.8)
                  for n, word in enumerate(text.split()))
    return Segment(start, words[-1].end, text, words, avg_logprob=-0.2)


class FakeTranscriber:
    name = "fake"
    model = "fake-1"

    def __init__(self, *lines):
        self.lines = lines or ("We sailed at dawn.", "Nobody saw us leave.")
        self.asked = []

    def transcribe(self, audio, *, language="en", prompt=None):
        self.asked.append((audio.name, language, prompt))
        return [sentence(line, start=index * 10.0) for index, line in enumerate(self.lines)]


class TranscriptsTest(DatabaseTest):
    def asset(self, name, medium="audio", has_audio=True, project=None, proxy=None):
        ((asset_id,),) = self.query(
            """INSERT INTO media.assets (medium, path, content_hash, has_audio, project, proxy_path)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
            (medium, f"/made-up/{name}", f"made-up-{name}", has_audio, project, proxy),
        )
        return asset_id

    def transcribe(self, transcriber=None, **options):
        return transcripts.transcribe(r"^/made-up/", transcriber=transcriber or FakeTranscriber(), **options)

    def segments(self, asset_id):
        return self.query(
            "SELECT idx, text, text_asr, model FROM media_video.segments WHERE asset_id = %s ORDER BY idx",
            (asset_id,),
        )

    def test_a_transcript_is_written_as_segments_and_their_words(self):
        asset = self.asset("harbour.wav")
        self.assertEqual(self.transcribe(), [("harbour.wav", 2)])
        self.assertEqual(self.segments(asset), [
            (0, "We sailed at dawn.", "We sailed at dawn.", "fake-1"),
            (1, "Nobody saw us leave.", "Nobody saw us leave.", "fake-1"),
        ])
        words = self.query(
            """SELECT s.idx, w.idx, w.word, w.start_s, w.end_s
                 FROM media_video.words w JOIN media_video.segments s ON s.id = w.segment_id
                WHERE s.asset_id = %s ORDER BY s.idx, w.idx""",
            (asset,),
        )
        self.assertEqual([(s, w, word) for s, w, word, _, _ in words], [
            (0, 0, "We"), (0, 1, "sailed"), (0, 2, "at"), (0, 3, "dawn."),
            (1, 0, "Nobody"), (1, 1, "saw"), (1, 2, "us"), (1, 3, "leave."),
        ])
        self.assertEqual([(start, end) for *_, start, end in words[4:6]], [(10.0, 10.4), (10.5, 10.9)])
        ((start, end, sureness, silence, ratio),) = self.query(
            """SELECT start_s, end_s, avg_logprob, no_speech_prob, compression_ratio
                 FROM media_video.segments WHERE asset_id = %s AND idx = 1""",
            (asset,),
        )
        self.assertEqual((start, end, sureness, silence), (10.0, 11.9, -0.2, None))
        self.assertGreater(ratio, 0)

    def test_only_recordings_with_sound_are_listened_to(self):
        self.asset("talk.mp4", medium="video")
        self.asset("silent.mp4", medium="video", has_audio=False)
        self.asset("card.png", medium="image", has_audio=False)
        self.assertEqual([name for name, _ in self.transcribe()], ["talk.mp4"])

    def test_the_proxy_is_what_is_listened_to_when_there_is_one(self):
        self.asset("camera.mov", medium="video", proxy="/made-up/work/camera-proxy.mp4")
        listener = FakeTranscriber()
        self.assertEqual(self.transcribe(listener), [("camera.mov", 2)])
        self.assertEqual(listener.asked, [("camera-proxy.mp4", "en", None)])

    def test_a_recording_is_transcribed_once(self):
        self.asset("harbour.wav")
        self.transcribe()
        again = FakeTranscriber()
        self.assertEqual(self.transcribe(again), [])
        self.assertEqual(again.asked, [])

    def test_only_what_matches_is_transcribed(self):
        self.asset("harbour.wav")
        self.asset("market.wav")
        done = transcripts.transcribe(r"^/made-up/mark", transcriber=FakeTranscriber())
        self.assertEqual(done, [("market.wav", 2)])

    def test_force_replaces_a_transcript_and_the_passages_cut_from_it(self):
        asset, other = self.asset("harbour.wav"), self.asset("market.wav")
        self.transcribe()
        for name, recording in (("made-up harbour", asset), ("made-up market", other)):
            ((take,),) = self.query("INSERT INTO media.takes (name) VALUES (%s) RETURNING id", (name,))
            self.query("INSERT INTO media.take_assets (take_id, asset_id, role) VALUES (%s, %s, 'lav') RETURNING 1",
                       (take, recording))
            self.query("INSERT INTO media_video.passages (take_id, start_s, end_s) VALUES (%s, 0, 5) RETURNING 1",
                       (take,))

        done = transcripts.transcribe(r"harbour", force=True, transcriber=FakeTranscriber("The tide is in."))
        self.assertEqual(done, [("harbour.wav", 1)])
        self.assertEqual([text for _, text, _, _ in self.segments(asset)], ["The tide is in."])
        self.assertEqual(len(self.segments(other)), 2)
        left = self.query(
            """SELECT t.name FROM media_video.passages p JOIN media.takes t ON t.id = p.take_id
                WHERE t.name LIKE 'made-up %%'""")
        self.assertEqual(left, [("made-up market",)])

    def test_a_recording_with_nothing_said_is_left_to_be_heard_again(self):
        asset = self.asset("wind.wav")
        self.assertEqual(self.transcribe(FakeTranscriber("[MUSIC]")), [("wind.wav", 0)])
        self.assertEqual(self.segments(asset), [])
        self.assertEqual(self.transcribe(), [("wind.wav", 2)])

    def test_a_recording_hears_its_projects_vocabulary_and_the_vocabulary_of_none(self):
        self.asset("a.wav", project="made-up-harbour")
        self.asset("b.wav", project="made-up-market")
        self.asset("c.wav")
        before = transcripts.terms()
        transcripts.learn(["Zentrova"])
        transcripts.learn(["Ottoline Varga"], project="made-up-harbour")
        transcripts.learn(["Quillfeather"], project="made-up-market")

        listener = FakeTranscriber()
        self.transcribe(listener)
        heard = {name: set(prompt.split(", ")) - set(before) for name, _, prompt in listener.asked}
        self.assertEqual(heard, {
            "a.wav": {"Zentrova", "Ottoline Varga"},
            "b.wav": {"Zentrova", "Quillfeather"},
            "c.wav": {"Zentrova"},
        })

    def test_another_projects_vocabulary_can_be_listened_for(self):
        self.asset("a.wav", project="made-up-harbour")
        self.asset("c.wav")
        before = transcripts.terms()
        transcripts.learn(["Zentrova"])
        transcripts.learn(["Ottoline Varga"], project="made-up-harbour")
        transcripts.learn(["Quillfeather"], project="made-up-market")
        listener = FakeTranscriber()
        self.transcribe(listener, vocabulary_of="made-up-market")
        self.assertEqual({name: set(prompt.split(", ")) - set(before) for name, _, prompt in listener.asked},
                         {"a.wav": {"Zentrova", "Quillfeather"}, "c.wav": {"Zentrova", "Quillfeather"}})

    def test_the_vocabulary_can_be_left_out(self):
        self.asset("a.wav")
        transcripts.learn(["Zentrova"])
        listener = FakeTranscriber()
        self.transcribe(listener, vocabulary=False, language="fr")
        self.assertEqual(listener.asked, [("a.wav", "fr", None)])

    def test_the_vocabulary_read_back_is_not_written_down(self):
        asset = self.asset("a.wav", project="made-up-harbour")
        transcripts.learn(["Zentrova", "Ottoline Varga", "Quillfeather"], project="made-up-harbour")
        listener = FakeTranscriber("Zentrova, Ottoline Varga, Quillfeather.", "Zentrova opened the harbour office in spring.")
        self.assertEqual(self.transcribe(listener), [("a.wav", 1)])
        self.assertEqual([text for _, text, _, _ in self.segments(asset)],
                         ["Zentrova opened the harbour office in spring."])

    def test_a_term_is_learned_once_for_each_project_and_once_for_none(self):
        self.assertEqual(transcripts.learn(["Zentrova", " Zentrova ", "", "Quillfeather"], kind="company"), 2)
        self.assertEqual(transcripts.learn(["Zentrova"]), 0)
        self.assertEqual(transcripts.learn(["Zentrova"], project="made-up-harbour"), 1)
        self.assertEqual(transcripts.learn(["Zentrova"], project="made-up-harbour"), 0)
        self.assertEqual(self.query(
            "SELECT project, kind FROM media_video.vocabulary WHERE term = 'Zentrova' ORDER BY project NULLS FIRST"),
            [(None, "company"), ("made-up-harbour", None)])
        listed = transcripts.terms("made-up-harbour")
        self.assertEqual(listed.count("Zentrova"), 2)
        self.assertIn("Quillfeather", listed)

    def test_no_recogniser_is_built_when_nothing_is_waiting(self):
        with mock.patch("constructive_media.audio.transcripts.default_transcriber") as built:
            self.assertEqual(transcripts.transcribe(r"^/made-up/nothing-here"), [])
        built.assert_not_called()


if __name__ == "__main__":
    unittest.main()
