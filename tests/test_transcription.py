"""Transcription: what is kept of a recogniser's output, how speech is packed
for it, and the whisper.cpp adapter driven with stand-in programs.

Nothing here runs a model. The adapter is given two small scripts in place of
whisper.cpp's programs: each writes down how it was called and answers with
what the test told it to. One test does run the real thing, and exists only
when `CONSTRUCTIVE_MEDIA_TEST_SPEECH` names a recording of someone speaking.
"""

import json
import math
import os
import re
import stat
import sys
import tempfile
import unittest
import wave
from array import array
from pathlib import Path

from constructive_media import adapters
from constructive_media.models import ModelError
from constructive_media.settings import Settings
from constructive_media.transcription import (
    LONGEST_SENTENCE_WORDS, Segment, Transcriber, TranscriptionError, Word,
    clean, echoes, is_caption, regions, sentences, whispercpp,
)
from constructive_media.transcription.regions import Region

from .support import make_tone, needs_ffmpeg, settings_in

SPEECH_VARIABLE = "CONSTRUCTIVE_MEDIA_TEST_SPEECH"
VOCABULARY = "Zentrova, Ottoline Varga, harbour office"


def spoken(text, start=0.0, each=0.5, probability=None):
    """A segment whose words are `text` split on spaces, said one after another."""
    words = tuple(
        Word(start + index * each, start + (index + 1) * each, word, probability)
        for index, word in enumerate(text.split())
    )
    return Segment(start, start + len(words) * each, text, words)


class CleanTest(unittest.TestCase):
    def test_speech_is_kept_as_it_was_said(self):
        heard = [spoken("We sailed at dawn."), spoken("Nobody saw us leave.", start=2.0)]
        self.assertEqual([s.text for s in clean(heard)], ["We sailed at dawn.", "Nobody saw us leave."])

    def test_empty_segments_and_captions_for_sounds_are_dropped(self):
        heard = [Segment(0, 1, "  "), Segment(1, 2, "[MUSIC]"), Segment(2, 3, "(applause)"),
                 Segment(3, 4, "*laughs*"), Segment(4, 5, "♪ ♪"), Segment(5, 6, "Good evening.")]
        self.assertEqual([s.text for s in clean(heard)], ["Good evening."])

    def test_a_caption_is_only_a_caption_when_it_is_all_there_is(self):
        self.assertTrue(is_caption("[BLANK_AUDIO]"))
        self.assertTrue(is_caption("(wind blowing)."))
        self.assertFalse(is_caption("She said (quietly) that it was done."))
        self.assertFalse(is_caption("Good evening."))

    def test_the_vocabulary_read_back_is_dropped(self):
        heard = [Segment(0, 2, "Zentrova, Ottoline Varga, harbour office."),
                 Segment(2, 4, "Glossary: Zentrova, Ottoline Varga."),
                 Segment(4, 8, "I work at the harbour office for Zentrova with Ottoline Varga.")]
        self.assertEqual([s.text for s in clean(heard, VOCABULARY)],
                         ["I work at the harbour office for Zentrova with Ottoline Varga."])
        self.assertEqual(len(clean(heard)), 3)

    def test_a_sentence_made_of_the_vocabulary_is_still_speech(self):
        self.assertTrue(echoes("Zentrova, Ottoline Varga", VOCABULARY))
        self.assertFalse(echoes("So we moved the harbour office to Zentrova when it was ready.", VOCABULARY))
        self.assertFalse(echoes("The tide comes in at six.", VOCABULARY))

    def test_a_line_repeated_over_and_over_is_a_loop_and_only_its_first_is_kept(self):
        line = "Thank you for watching."
        heard = [spoken("We are done here.")] + [spoken(line, start=2.0 + n) for n in range(6)] \
            + [spoken("One more thing.", start=9.0)]
        self.assertEqual([s.text for s in clean(heard)], ["We are done here.", line, "One more thing."])

    def test_saying_something_twice_is_not_a_loop(self):
        heard = [spoken("No no no."), spoken("No no no.", start=2.0), spoken("Yes.", start=4.0),
                 spoken("Yes.", start=5.0), spoken("Yes.", start=6.0), spoken("Yes.", start=7.0)]
        self.assertEqual(len(clean(heard)), 6)

    def test_a_word_that_ends_before_it_starts_is_given_a_length(self):
        heard = Segment(0, 3, "one two three", (Word(1.0, 1.0, " one"), Word(1.1, 0.9, "two"), Word(2.0, 2.0, "three")))
        first, second, third = clean([heard])[0].words
        self.assertEqual((first.text, first.start), ("one", 1.0))
        self.assertAlmostEqual(first.end, 1.1)   # stops where the next begins
        self.assertAlmostEqual(second.end, 1.4)  # the assumed length of a word
        self.assertAlmostEqual(third.end, 2.3)

    def test_words_with_no_text_are_dropped(self):
        heard = Segment(0, 1, "well", (Word(0, 0.5, " "), Word(0.5, 1.0, "well")))
        self.assertEqual([w.text for w in clean([heard])[0].words], ["well"])


class SentencesTest(unittest.TestCase):
    def test_a_segment_is_cut_into_its_sentences(self):
        parts = sentences(spoken("We sailed at dawn. Did anyone see? Nobody! So we kept going."))
        self.assertEqual([p.text for p in parts],
                         ["We sailed at dawn.", "Did anyone see?", "Nobody!", "So we kept going."])
        self.assertEqual([(p.start, p.end) for p in parts], [(0.0, 2.0), (2.0, 3.5), (3.5, 4.0), (4.0, 6.0)])
        self.assertEqual([len(p.words) for p in parts], [4, 3, 1, 4])

    def test_a_full_stop_does_not_always_end_a_sentence(self):
        for text in ("Dr. Varga met Mr. Quill at 3.5 knots.", "It was e.g. this one.",
                     "Well... what now", "Deck no. 4 is closed."):
            with self.subTest(text=text):
                self.assertEqual(len(sentences(spoken(text))), 1)

    def test_quotes_and_brackets_close_before_the_sentence_ends(self):
        parts = sentences(spoken('She said "cast off." Then we left.'))
        self.assertEqual([p.text for p in parts], ['She said "cast off."', "Then we left."])

    def test_a_segment_with_no_words_is_left_alone(self):
        whole = Segment(0, 4, "One thing. Another thing.")
        self.assertEqual(sentences(whole), [whole])

    def test_a_run_with_no_full_stop_is_broken_at_its_longest_pause(self):
        count = LONGEST_SENTENCE_WORDS + 20
        words = [Word(n * 0.5, n * 0.5 + 0.4, f"word{n}") for n in range(count)]
        words[25:] = [Word(w.start + 2.0, w.end + 2.0, w.text) for w in words[25:]]  # a breath
        parts = sentences(Segment(0, words[-1].end, " ".join(w.text for w in words), tuple(words)))
        self.assertEqual([len(p.words) for p in parts], [25, count - 25])
        self.assertEqual(parts[1].words[0].text, "word25")
        self.assertTrue(all(len(p.words) <= LONGEST_SENTENCE_WORDS for p in parts))

    def test_text_written_without_spaces_is_joined_without_them(self):
        whole = Segment(0, 2, "今日は。晴れ。", (Word(0, 1, "今日は。"), Word(1, 2, "晴れ。")))
        self.assertEqual([p.text for p in sentences(whole)], ["今日は。", "晴れ。"])

    def test_each_sentence_is_as_sure_as_its_own_words(self):
        whole = Segment(0, 2, "Sure thing. Maybe not.", (
            Word(0, 0.5, "Sure", 0.9), Word(0.5, 1, "thing.", 0.9),
            Word(1, 1.5, "Maybe", 0.1), Word(1.5, 2, "not.", 0.1),
        ), avg_logprob=-0.5)
        sure, unsure = sentences(whole)
        self.assertAlmostEqual(sure.avg_logprob, math.log(0.9))
        self.assertAlmostEqual(unsure.avg_logprob, math.log(0.1))

    def test_without_a_sureness_for_each_word_a_sentence_keeps_the_segments(self):
        whole = spoken("One thing. Another thing.")
        self.assertEqual({p.avg_logprob for p in sentences(Segment(**{**vars(whole), "avg_logprob": -0.4}))}, {-0.4})


class SegmentTest(unittest.TestCase):
    def test_repeated_text_compresses_and_speech_does_not(self):
        self.assertLess(Segment(0, 1, "The tide comes in at six and the boats go out.").compression_ratio, 2.4)
        self.assertGreater(Segment(0, 1, "and then " * 60).compression_ratio, 2.4)
        self.assertIsNone(Segment(0, 1, "").compression_ratio)


def write_wav(path, seconds, rate=1000):
    """A WAV whose every sample is its own position, so a copy can be traced."""
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(array("h", range(round(seconds * rate))).tobytes())
    return path


def samples(path):
    with wave.open(str(path), "rb") as recording:
        found = array("h")
        found.frombytes(recording.readframes(recording.getnframes()))
    if sys.byteorder == "big":
        found.byteswap()
    return list(found)


class RegionsTest(unittest.TestCase):
    def test_regions_are_ordered_padded_and_kept_inside_the_recording(self):
        found = regions.joined([Region(5.0, 6.0), Region(0.02, 1.0), Region(9.5, 9.99)], 10.0, pad=0.05, within=0.2)
        self.assertEqual([(round(r.start, 2), round(r.end, 2)) for r in found], [(0.0, 1.05), (4.95, 6.05), (9.45, 10.0)])

    def test_regions_a_breath_apart_are_one(self):
        found = regions.joined([Region(1.0, 2.0), Region(2.15, 3.0), Region(4.0, 5.0)], 10.0, pad=0.0, within=0.2)
        self.assertEqual(found, [Region(1.0, 3.0), Region(4.0, 5.0)])

    def test_regions_are_not_joined_past_the_longest_allowed(self):
        found = regions.joined([Region(0.0, 20.0), Region(20.1, 30.0)], 40.0, pad=0.0, within=0.2, longest=28.0)
        self.assertEqual(found, [Region(0.0, 20.0), Region(20.1, 30.0)])

    def test_padding_never_reaches_back_into_the_region_before(self):
        found = regions.joined([Region(0.0, 20.0), Region(20.02, 30.0)], 40.0, pad=0.05, within=0.0, longest=28.0)
        self.assertEqual(found[1].start, found[0].end)

    def test_regions_are_grouped_into_runs_that_fit(self):
        # 10 + 10 + 8 seconds of speech and two pauses would be 28.4: one too many.
        runs = regions.grouped([Region(0, 10), Region(11, 21), Region(22, 30), Region(31, 32)], 28.0, pause=0.2)
        self.assertEqual([[(r.start, r.end) for r in run] for run in runs],
                         [[(0, 10), (11, 21)], [(22, 30), (31, 32)]])

    def test_a_region_too_long_to_fit_is_a_run_by_itself(self):
        runs = regions.grouped([Region(0, 5), Region(6, 46), Region(47, 50)], 28.0)
        self.assertEqual([len(run) for run in runs], [1, 1, 1])
        self.assertEqual(regions.grouped([], 28.0), [])

    def test_packing_keeps_the_speech_and_puts_a_pause_between(self):
        with tempfile.TemporaryDirectory() as folder:
            source = write_wav(Path(folder) / "recording.wav", 10.0)
            packed = regions.pack(source, Path(folder) / "packed.wav", [Region(1.0, 2.0), Region(5.0, 6.5)], pause=0.2)
            heard = samples(Path(folder) / "packed.wav")
            self.assertEqual(regions.duration(Path(folder) / "packed.wav"), 2.7)
        self.assertEqual(heard[:1000], list(range(1000, 2000)))
        self.assertEqual(heard[1000:1200], [0] * 200)
        self.assertEqual(heard[1200:], list(range(5000, 6500)))
        self.assertEqual(packed.original(0.5), 1.5)
        self.assertEqual(packed.original(1.1), 2.0)   # in the pause: the end of what came before
        self.assertEqual(packed.original(1.7), 5.5)
        self.assertEqual(packed.original(99.0), 6.5)

    def test_a_span_across_a_pause_stays_in_the_region_holding_most_of_it(self):
        packed = regions.Packed([(0.0, Region(1.0, 2.0)), (1.2, Region(5.0, 6.5))])
        self.assertEqual(packed.span(0.2, 0.6), (1.2, 1.6))
        self.assertEqual(packed.span(0.9, 1.9), (5.0, 5.7))   # mostly the second
        self.assertEqual(packed.span(0.3, 1.3), (1.3, 2.0))   # mostly the first

    def test_a_file_too_short_to_recognise_is_filled_out_with_silence(self):
        with tempfile.TemporaryDirectory() as folder:
            source = write_wav(Path(folder) / "recording.wav", 4.0)
            packed = regions.pack(source, Path(folder) / "packed.wav", [Region(1.0, 1.3)], shortest=1.2)
            heard = samples(Path(folder) / "packed.wav")
        self.assertEqual(len(heard), 1200)
        self.assertEqual(heard[300:], [0] * 900)
        self.assertEqual(packed.span(0.1, 0.9), (1.1, 1.3))

    def test_regions_outside_the_recording_pack_to_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            source = write_wav(Path(folder) / "recording.wav", 2.0)
            packed = regions.pack(source, Path(folder) / "packed.wav", [Region(5.0, 6.0)])
        self.assertEqual((packed.original(0.5), packed.span(0.1, 0.4)), (0.0, (0.0, 0.0)))


def token(text, ends_cs=-1, p=0.9, span=(0, 0)):
    """A whisper.cpp token: its text, where attention says it ends (in
    hundredths of a second) and the span the model itself guessed (in ms)."""
    return {"text": text, "p": p, "t_dtw": ends_cs, "offsets": {"from": span[0], "to": span[1]}}


def said(start_ms, end_ms, *tokens):
    """A whisper.cpp segment, opened and closed with the marks Whisper adds."""
    return {
        "offsets": {"from": start_ms, "to": end_ms},
        "text": "".join(t["text"] for t in tokens),
        "tokens": [token("[_BEG_]"), *tokens, token(f"[_TT_{end_ms // 20}]")],
    }


CLI = """#!{python}
import json, sys
from pathlib import Path
here = Path(__file__).parent
arguments = sys.argv[1:]
with open(here / "recognised.jsonl", "a") as log:
    log.write(json.dumps(arguments) + "\\n")
replies = json.loads((here / "replies.json").read_text())
for wav in (Path(a) for a in arguments if a.endswith(".wav")):
    Path(str(wav) + ".json").write_text(json.dumps({{"transcription": replies.get(wav.stem, [])}}))
"""

SPEECH = """#!{python}
import json, sys
from pathlib import Path
here = Path(__file__).parent
with open(here / "listened.jsonl", "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
sys.stdout.write((here / "speech.txt").read_text())
"""

BROKEN = """#!{python}
import sys
sys.stderr.write("loading the model\\nerror: this is not a model file\\n")
sys.exit(3)
"""


class StandIns:
    """A folder with stand-ins for whisper.cpp's programs and for its models."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.settings = settings_in(self.folder / "home")
        models = self.settings.models_dir / "transcription"
        models.mkdir(parents=True)
        (models / whispercpp.SPEECH_MODEL).write_bytes(b"stand-in")  # there already, so never fetched
        self.cli = self.program("whisper-cli", CLI)
        self.speech_program = self.program("whisper-vad-speech-segments", SPEECH)
        self.answer({})
        self.speech()

    def program(self, name, source):
        path = self.folder / name
        path.write_text(source.format(python=sys.executable))
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return str(path)

    def answer(self, replies):
        """What whisper-cli writes for each file, by the file's name without `.wav`."""
        (self.folder / "replies.json").write_text(json.dumps(replies))

    def speech(self, *spans):
        """Where the speech program says the speech is, in seconds."""
        (self.folder / "speech.txt").write_text("".join(
            f"Speech segment {n}: start = {start * 100:.2f}, end = {end * 100:.2f}\n"
            for n, (start, end) in enumerate(spans)
        ))

    def transcriber(self, model="ggml-base.bin", **options):
        weights = self.folder / model
        weights.write_bytes(b"stand-in")
        return whispercpp.WhisperCpp(str(weights), settings=self.settings, program=self.cli,
                                     speech_program=self.speech_program, **options)

    def calls(self, log="recognised.jsonl"):
        path = self.folder / log
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def option(call, name):
    """The value that follows an option in a command line."""
    return call[call.index(name) + 1]


def files(call):
    return [Path(argument).name for argument in call if argument.endswith(".wav")]


@needs_ffmpeg
class WhisperCppTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(folder.cleanup)
        cls.short = make_tone(Path(folder.name) / "short.wav", seconds=4.0)
        cls.long = make_tone(Path(folder.name) / "long.wav", seconds=40.0)

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.stand_ins = StandIns(folder.name)

    def test_it_is_a_transcriber(self):
        self.assertIsInstance(self.stand_ins.transcriber(), Transcriber)

    def test_only_the_speech_is_heard_and_every_time_is_put_back_on_the_recording(self):
        # Speech from 1.0 to 2.0 and from 3.0 to 3.5. With a little kept either
        # side, the packed file holds 0.95-2.05, a 0.2 s pause, then 2.95-3.55.
        self.stand_ins.speech((1.0, 2.0), (3.0, 3.5))
        self.stand_ins.answer({"speech-00000": [said(
            0, 1900, token(" Hello", 50), token(" there", 100), token(".", 105),
            token(" Again", 170), token(".", 180),
        )]})
        (segment,) = self.stand_ins.transcriber().transcribe(self.short)

        self.assertEqual(segment.text, "Hello there. Again.")
        self.assertEqual([w.text for w in segment.words], ["Hello", "there.", "Again."])
        times = [(round(w.start, 2), round(w.end, 2)) for w in segment.words]
        self.assertEqual(times, [(0.95, 1.45), (1.45, 2.0), (2.95, 3.45)])
        self.assertEqual((round(segment.start, 2), round(segment.end, 2)), (0.95, 3.45))
        self.assertAlmostEqual(segment.avg_logprob, math.log(0.9))
        self.assertAlmostEqual(segment.words[0].probability, 0.9)
        self.assertEqual([s.text for s in clean([segment])], ["Hello there.", "Again."])

        (call,) = self.stand_ins.calls()
        self.assertEqual(files(call), ["speech-00000.wav"])
        self.assertEqual((option(call, "-l"), option(call, "-mc"), option(call, "-dtw")), ("en", "0", "base"))
        self.assertIn("-nfa", call)   # attention cannot be read with the fast path on
        self.assertNotIn("--prompt", call)
        self.assertNotIn("-ng", call)

    def test_speech_is_handed_over_in_pieces_that_fit_a_window(self):
        self.stand_ins.speech((0.0, 20.0), (21.0, 30.0), (31.0, 39.0))
        self.stand_ins.answer({
            "speech-00000": [said(0, 20000, token(" First", 100), token(".", 110))],
            "speech-00001": [said(0, 17000, token(" Second", 100), token(".", 110)),
                             said(9300, 17000, token(" Third", 1000), token(".", 1010))],
        })
        heard = self.stand_ins.transcriber().transcribe(self.long)
        self.assertEqual([s.text for s in heard], ["First.", "Second.", "Third."])
        self.assertEqual([round(s.start, 2) for s in heard], [0.0, 20.95, 30.95])
        (call,) = self.stand_ins.calls()   # every piece in one run: the model is loaded once
        self.assertEqual(files(call), ["speech-00000.wav", "speech-00001.wav"])

    def test_a_vocabulary_is_shown_only_to_pieces_that_fit_one_window(self):
        self.stand_ins.speech((0.0, 35.0), (37.0, 39.0))   # the first has no pause to cut at
        self.stand_ins.answer({
            "speech-00000": [said(0, 35000, token(" Long", 100), token(".", 110))],
            "speech-00001": [said(0, 2000, token(" Short", 50), token(".", 60))],
        })
        heard = self.stand_ins.transcriber("ggml-large-v3-turbo.bin").transcribe(self.long, prompt=VOCABULARY)
        self.assertEqual([s.text for s in heard], ["Long.", "Short."])

        shown, plain = self.stand_ins.calls()
        self.assertEqual(files(shown), ["speech-00001.wav"])
        self.assertEqual(option(shown, "--prompt"), f"Glossary: {VOCABULARY}.")
        self.assertNotIn("-mc", shown)   # it would switch the prompt off
        self.assertEqual(files(plain), ["speech-00000.wav"])
        self.assertNotIn("--prompt", plain)
        self.assertEqual(option(plain, "-mc"), "0")
        self.assertEqual(option(plain, "-dtw"), "large.v3.turbo")

    def test_the_smallest_models_are_never_shown_a_vocabulary(self):
        self.stand_ins.speech((1.0, 2.0))
        self.stand_ins.transcriber("ggml-base.en.bin").transcribe(self.short, prompt=VOCABULARY)
        (call,) = self.stand_ins.calls()
        self.assertNotIn("--prompt", call)
        self.assertEqual(option(call, "-mc"), "0")

    def test_a_recording_with_no_speech_has_no_transcript_and_the_model_is_never_run(self):
        self.assertEqual(self.stand_ins.transcriber().transcribe(self.short), [])
        self.assertEqual(self.stand_ins.calls(), [])
        (listened,) = self.stand_ins.calls("listened.jsonl")
        self.assertEqual(Path(option(listened, "-vm")).name, whispercpp.SPEECH_MODEL)

    def test_the_whole_recording_can_be_heard_instead(self):
        self.stand_ins.answer({"heard": [
            said(500, 1500, token(" Good", span=(500, 900)), token(" morning", span=(900, 1400)),
                 token(".", span=(1400, 1500))),
        ]})
        # Not a model whisper.cpp can read attention from: the tokens' own spans are used.
        transcriber = self.stand_ins.transcriber("custom.bin", speech_only=False)
        (segment,) = transcriber.transcribe(self.short)
        self.assertEqual(transcriber.model, "custom")
        self.assertEqual([(w.text, w.start, w.end) for w in segment.words],
                         [("Good", 0.5, 0.9), ("morning.", 0.9, 1.5)])
        (call,) = self.stand_ins.calls()
        self.assertEqual(files(call), ["heard.wav"])
        self.assertNotIn("-dtw", call)
        self.assertEqual(self.stand_ins.calls("listened.jsonl"), [])

    def test_the_cpu_can_be_insisted_on_and_the_language_chosen(self):
        self.stand_ins.speech((1.0, 2.0))
        self.stand_ins.transcriber(device="cpu").transcribe(self.short, language="fr")
        (call,) = self.stand_ins.calls()
        self.assertIn("-ng", call)
        self.assertEqual(option(call, "-l"), "fr")
        self.assertNotIn("-ng", self.stand_ins.calls("listened.jsonl")[0])   # not an option it has

    def test_speech_reported_past_the_end_of_the_recording_is_refused(self):
        self.stand_ins.speech((100.0, 200.0))
        with self.assertRaisesRegex(TranscriptionError, "past the end"):
            self.stand_ins.transcriber().transcribe(self.short)

    def test_a_program_that_fails_is_reported_by_its_last_line(self):
        self.stand_ins.speech((1.0, 2.0))
        self.stand_ins.cli = self.stand_ins.program("broken", BROKEN)
        with self.assertRaisesRegex(TranscriptionError, "broken failed: error: this is not a model file$"):
            self.stand_ins.transcriber().transcribe(self.short)


class WhisperCppWithoutProgramsTest(unittest.TestCase):
    def test_a_missing_program_says_what_to_install(self):
        with self.assertRaisesRegex(TranscriptionError, "install whisper.cpp"):
            whispercpp._run("no-such-program-anywhere")

    def test_a_model_that_is_neither_known_nor_a_file_is_explained(self):
        with tempfile.TemporaryDirectory() as folder:
            transcriber = whispercpp.WhisperCpp("enormous-v9", settings=settings_in(folder))
            with self.assertRaisesRegex(ModelError, "large-v3-turbo"):
                transcriber.weights()

    def test_the_model_comes_from_the_argument_then_settings_then_the_default(self):
        with tempfile.TemporaryDirectory() as folder:
            chosen = settings_in(folder, models={"transcription": "small"})
            self.assertEqual(whispercpp.WhisperCpp("tiny", settings=chosen).model, "tiny")
            self.assertEqual(whispercpp.WhisperCpp(settings=chosen).model, "small")
            self.assertEqual(whispercpp.WhisperCpp(settings=settings_in(folder)).model, whispercpp.DEFAULT_MODEL)

    def test_a_squeezed_model_aligns_as_its_family(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = settings_in(folder)
            self.assertEqual(whispercpp.WhisperCpp("large-v3-turbo-q5_0", settings=settings).alignment(), "large.v3.turbo")
            self.assertEqual(whispercpp.WhisperCpp("base.en", settings=settings).alignment(), "base.en")
            self.assertIsNone(whispercpp.WhisperCpp("/models/ggml-custom.bin", settings=settings).alignment())

    def test_every_known_model_is_permissive_and_pinned(self):
        self.assertIn(whispercpp.DEFAULT_MODEL, whispercpp.KNOWN)
        for name, known in whispercpp.KNOWN.items():
            with self.subTest(model=name):
                self.assertIn(known.license, adapters.PERMISSIVE)
                self.assertRegex(known.file.url, r"^https://huggingface\.co/.+/resolve/[0-9a-f]{40}/ggml-.+\.bin$")
                self.assertRegex(known.file.sha256, r"^[0-9a-f]{64}$")
        self.assertRegex(whispercpp.SPEECH_REMOTE.url, r"/resolve/[0-9a-f]{40}/")

    def test_whispers_own_marks_are_not_words_and_a_segment_of_them_is_nothing(self):
        written = [said(0, 1000), said(1000, 2000, token(" Aye", 150), token(".", 160))]
        (segment,) = whispercpp._segments(written, None, aligned=True, split=False)
        self.assertEqual((segment.text, [w.text for w in segment.words]), ("Aye.", ["Aye."]))

    def test_no_segment_starts_before_the_one_before_it_ended(self):
        written = [said(0, 3000, token(" One", 290), token(".", 300)),
                   said(2000, 5000, token(" Two", 450), token(".", 460))]
        first, second = whispercpp._segments(written, None, aligned=True, split=False)
        self.assertEqual((first.end, second.start), (3.0, 3.0))

    def test_a_language_written_without_spaces_is_timed_a_token_at_a_time(self):
        tokens = [token("今", 50), token("日", 100), token("は", 150)]
        self.assertEqual([w.text for w in whispercpp._words(tokens, 0.0, aligned=True, split=True)], ["今", "日", "は"])
        self.assertEqual([w.text for w in whispercpp._words(tokens, 0.0, aligned=True, split=False)], ["今日は"])

    def test_a_vocabulary_is_introduced_and_ended(self):
        self.assertEqual(whispercpp.worded(" Zentrova, Ottoline Varga. "), "Glossary: Zentrova, Ottoline Varga.")


class BuiltinTest(unittest.TestCase):
    def test_whisper_cpp_is_the_default_transcriber(self):
        described = adapters.registry().describe("transcription", settings=Settings.from_env({}))
        self.assertEqual((described.name, described.license, described.origin), ("whispercpp", "MIT", "builtin"))


if os.environ.get(SPEECH_VARIABLE):
    class RealWhisperCppTest(unittest.TestCase):
        """The real programs, on a recording of your own.

        Uses the model your settings choose, and downloads it if it is not
        in your models folder yet.
        """

        def test_a_recording_of_speech_becomes_timed_words_in_order(self):
            recording = Path(os.environ[SPEECH_VARIABLE])
            heard = clean(whispercpp.WhisperCpp().transcribe(recording))
            words = [word for segment in heard for word in segment.words]
            self.assertGreater(len(words), 3)
            self.assertTrue(all(word.end > word.start for word in words))
            self.assertEqual([w.start for w in words], sorted(w.start for w in words))
            self.assertTrue(all(re.search(r"\w", segment.text) for segment in heard))
            self.assertTrue(all(segment.avg_logprob <= 0 for segment in heard))


if __name__ == "__main__":
    unittest.main()
