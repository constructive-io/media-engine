"""Talking with a text model aloud: sentences, turns, the microphone, the WebSocket and the page's server.

No model runs here. A stand-in writes the answer it was given, piece by
piece, a stand-in says each sentence as a sound made of the sentence's own
letters, so a test can read back what was said, and a stand-in hears what it
is told to hear.
"""

import array
import http.client
import io
import json
import socket
import struct
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
import wave
from unittest import mock

from constructive_media import talk
from constructive_media.models import ModelError
from constructive_media.speech import SpeechError, Voice
from constructive_media.talk import hearing, measure, serve, websocket
from constructive_media.text import TextError
from constructive_media.transcription import Segment

from .support import needs_numpy, settings_in
from .test_cli import run

ADA = Voice("ada", profile="made-up-voice")


class Says:
    """A stand-in for a speech model: the sound of a sentence is its own letters."""

    name, model = "stand-in", "made-up-voice"

    def __init__(self, wrong=None):
        self.wrong, self.said = wrong, []

    def say(self, text, target, *, voice, seed=None):
        self.said.append((text, voice))
        if self.wrong is not None:
            raise self.wrong
        target.write_bytes(talk.wav(text.encode("utf-16-le"), 24_000))
        return target


def letters(sound):
    """The sentence a stand-in's sound was made of."""
    with wave.open(io.BytesIO(sound)) as file:
        return file.readframes(file.getnframes()).decode("utf-16-le")


class Answers:
    """A stand-in for a text model: each answer is the pieces it comes in.

    A piece that is an Event is waited for, which is how a test holds an
    answer part way. A piece that is an exception is raised.
    """

    name, model = "stand-in", "made-up-writer"

    def __init__(self, *answers):
        self.answers, self.asked, self.dropped = list(answers), [], 0

    def reply(self, messages, *, limit=400):
        self.asked.append([dict(message) for message in messages])
        try:
            for piece in self.answers.pop(0):
                if isinstance(piece, threading.Event):
                    piece.wait(5)
                elif isinstance(piece, Exception):
                    raise piece
                else:
                    yield piece
        except GeneratorExit:
            self.dropped += 1
            raise


class Hears:
    """A stand-in for a speech-to-text model: it hears what it is told to, and keeps what it was played."""

    name, model = "stand-in", "made-up-ears"

    def __init__(self, *heard):
        self.heard, self.played = list(heard), []

    def transcribe(self, audio, *, language="en", prompt=None):
        with wave.open(str(audio)) as file:
            self.played.append((file.getframerate(), file.getnchannels(), file.getsampwidth(), file.getnframes(), language))
        text = self.heard.pop(0) if len(self.heard) > 1 else self.heard[0]
        if isinstance(text, Exception):
            raise text
        return [Segment(0.0, 1.0, text)] if text else []


class Told:
    """Everything a conversation told, in order."""

    def __init__(self):
        self.all, self._more = [], threading.Condition()

    def __call__(self, told):
        with self._more:
            self.all.append(told)
            self._more.notify_all()

    def until(self, found, seconds=5):
        """Wait for something to have been told."""
        with self._more:
            if not self._more.wait_for(lambda: any(found(told) for told in self.all), seconds):
                raise AssertionError(f"never told: {self.shown()}")

    def shown(self):
        return [letters(told) if isinstance(told, bytes) else
                told.get("value") or told.get("text") or told["type"] for told in self.all]

    def of(self, kind):
        return [told for told in self.all if isinstance(told, dict) and told["type"] == kind]


def sound(told):
    return isinstance(told, bytes)


class SentencesTest(unittest.TestCase):
    def sentences(self, *pieces):
        return list(talk.sentences(pieces))

    def test_a_sentence_is_given_the_moment_it_closes(self):
        seen = []

        def pieces():
            for piece in ("The tide is ", "going out. It ", "turns at six."):
                seen.append(piece)
                yield piece

        coming = talk.sentences(pieces())
        self.assertEqual(next(coming), "The tide is going out.")
        # The second sentence had not been written when the first was handed over.
        self.assertEqual(seen, ["The tide is ", "going out. It "])
        self.assertEqual(list(coming), ["It turns at six."])

    def test_however_the_pieces_fall_the_sentences_are_the_same(self):
        answer = 'Is it? "Yes!" she said. Well… good. (All of it.) Done'
        whole = self.sentences(answer)
        self.assertEqual(whole, ["Is it?", '"Yes!"', "she said.", "Well…", "good.", "(All of it.)", "Done"])
        self.assertEqual(self.sentences(*answer), whole)
        self.assertEqual(self.sentences(*(answer[at:at + 7] for at in range(0, len(answer), 7))), whole)

    def test_a_stop_that_ends_nothing_does_not_end_a_sentence(self):
        self.assertEqual(self.sentences("Ask Dr. Okafor or J. Lind at 3.5 knots. ", "Use salt, e.g. sea salt. 1. Stir. "),
                         ["Ask Dr. Okafor or J. Lind at 3.5 knots.", "Use salt, e.g. sea salt.", "1. Stir."])

    def test_a_script_written_without_spaces_still_closes(self):
        self.assertEqual(self.sentences("潮が引く。", "六時に変わる。"), ["潮が引く。", "六時に変わる。"])

    def test_marks_around_a_sentence_close_with_it(self):
        self.assertEqual(self.sentences("**Well.** Fine."), ["**Well.**", "Fine."])

    def test_nothing_comes_of_nothing(self):
        self.assertEqual(self.sentences(), [])
        self.assertEqual(self.sentences("", "  ", "\n"), [])

    def test_thinking_is_taken_out_wherever_the_marks_fall(self):
        answer = "<think>They asked about the tide. Out.</think>It is going out. <think>And then?</think>It turns at six."
        for size in (1, 2, 3, 5, 8, len(answer)):
            with self.subTest(size=size):
                self.assertEqual(self.sentences(*(answer[at:at + size] for at in range(0, len(answer), size))),
                                 ["It is going out.", "It turns at six."])

    def test_what_only_looks_like_the_start_of_a_mark_is_kept(self):
        self.assertEqual(self.sentences("Is 2 <", " 3? And <th", "en> what"), ["Is 2 < 3?", "And <then> what"])
        self.assertEqual(self.sentences("It ends in <thi"), ["It ends in <thi"])

    def test_thinking_that_never_ends_says_nothing(self):
        self.assertEqual(self.sentences("<think>Hmm. Well. I wonder"), [])

    def test_what_a_page_would_show_is_not_said(self):
        self.assertEqual(talk.sayable("**Well.**  It is `six`, _really_.\n# So"), "Well. It is six, really. So")
        for nothing in ("", "***", "---", "…", "🙂.", "```"):
            with self.subTest(nothing=nothing):
                self.assertEqual(talk.sayable(nothing), "")

    def test_samples_become_a_recording(self):
        with wave.open(io.BytesIO(talk.wav(b"\x01\x00" * 800))) as file:
            self.assertEqual((file.getframerate(), file.getnchannels(), file.getsampwidth(), file.getnframes()),
                             (16_000, 1, 2, 800))


class ConversationTest(unittest.TestCase):
    def conversation(self, writer, *, says=None, hears=None, **more):
        self.says, self.told = says or Says(), Told()
        parts = talk.Parts(writer=writer, synthesizer=self.says, voice=ADA, transcriber=hears, **more)
        conversation = talk.Conversation(parts, self.told)
        self.addCleanup(conversation.close)
        return conversation

    def finish(self, conversation):
        self.assertTrue(conversation.wait(5), self.told.shown())

    def test_a_typed_turn_is_answered_a_sentence_at_a_time(self):
        writer = Answers(["The tide ", "is going out. ", "It turns ", "at six."])
        conversation = self.conversation(writer)
        conversation.typed("  What is the tide doing? ")
        self.finish(conversation)
        self.assertEqual(self.told.shown(), [
            "thinking", "What is the tide doing?", "speaking",
            "The tide is going out.", "The tide is going out.", "It turns at six.", "It turns at six.",
            "timing", "done", "listening",
        ])
        self.assertEqual([told["type"] if isinstance(told, dict) else "sound" for told in self.told.all],
                         ["state", "heard", "state", "said", "sound", "said", "sound", "timing", "done", "state"])
        self.assertEqual(writer.asked, [[{"role": "system", "content": talk.SYSTEM},
                                         {"role": "user", "content": "What is the tide doing?"}]])
        self.assertEqual(self.says.said, [("The tide is going out.", ADA), ("It turns at six.", ADA)])
        self.assertEqual(conversation.history, [
            {"role": "user", "content": "What is the tide doing?"},
            {"role": "assistant", "content": "The tide is going out. It turns at six."},
        ])
        (timing,) = self.told.of("timing")
        self.assertEqual(sorted(timing), ["first_sentence_ms", "first_sound_ms", "first_word_ms", "type"])
        self.assertLessEqual(timing["first_word_ms"], timing["first_sentence_ms"])

    def test_the_first_sentence_is_heard_while_the_rest_is_being_written(self):
        rest = threading.Event()
        conversation = self.conversation(Answers(["It is going out. ", "It ", rest, "turns at six."]))
        conversation.typed("And the tide?")
        self.told.until(sound)
        self.assertEqual(self.told.shown(), ["thinking", "And the tide?", "speaking", "It is going out.", "It is going out."])
        rest.set()
        self.finish(conversation)
        self.assertEqual(self.says.said[-1][0], "It turns at six.")

    def test_the_next_turn_is_shown_what_was_said_before(self):
        writer = Answers(["Out."], ["At six."])
        conversation = self.conversation(writer, system="You know the tides.")
        for said in ("What is the tide doing?", "When does it turn?"):
            conversation.typed(said)
            self.finish(conversation)
        self.assertEqual(writer.asked[1], [
            {"role": "system", "content": "You know the tides."},
            {"role": "user", "content": "What is the tide doing?"},
            {"role": "assistant", "content": "Out."},
            {"role": "user", "content": "When does it turn?"},
        ])

    def test_only_so_many_turns_are_remembered(self):
        writer = Answers(["One."], ["Two."], ["Three."])
        conversation = self.conversation(writer, remembered=1)
        for said in ("first", "second", "third"):
            conversation.typed(said)
            self.finish(conversation)
        self.assertEqual([message["content"] for message in writer.asked[2]], [talk.SYSTEM, "Two.", "third"])
        self.assertEqual(len(conversation.history), 6)

    def test_an_answer_talked_over_stops_where_it_is(self):
        rest = threading.Event()
        writer = Answers(["One. ", rest, "Two. Three."])
        conversation = self.conversation(writer)
        conversation.typed("Count for me")
        self.told.until(sound)
        conversation.hush()
        rest.set()
        self.finish(conversation)
        self.assertEqual(self.told.shown(), ["thinking", "Count for me", "speaking", "One.", "One.", "hush", "listening"])
        # Nothing was said after it was told to stop, and only what was said is remembered as said.
        self.assertEqual([said for said, _ in self.says.said], ["One."])
        self.assertEqual(conversation.history[-1], {"role": "assistant", "content": "One."})
        self.assertEqual(writer.dropped, 1)

    def test_there_is_nothing_to_hush_when_nothing_is_being_said(self):
        conversation = self.conversation(Answers())
        conversation.hush()
        self.assertEqual(self.told.shown(), ["listening"])

    def test_a_new_turn_takes_the_place_of_one_still_being_answered(self):
        rest = threading.Event()
        writer = Answers([rest, "It is going out."], ["At six, then."])
        conversation = self.conversation(writer)
        conversation.typed("What is the tide doing?")
        self.told.until(lambda told: told == {"type": "heard", "text": "What is the tide doing?"})
        conversation.typed("I mean, when does it turn?")
        rest.set()
        self.finish(conversation)
        self.assertEqual(self.told.shown(), [
            "thinking", "What is the tide doing?", "thinking", "I mean, when does it turn?",
            "speaking", "At six, then.", "At six, then.", "timing", "done", "listening",
        ])
        # The answer that was never said is not remembered, and both things asked are asked as one.
        self.assertEqual(writer.asked[1][1:], [
            {"role": "user", "content": "What is the tide doing?\nI mean, when does it turn?"}])
        self.assertEqual([said for said, _ in self.says.said], ["At six, then."])

    def test_a_turn_taken_while_a_sentence_plays_hushes_it_first(self):
        rest = threading.Event()
        conversation = self.conversation(Answers(["One. ", rest, "Two."], ["Fine."]))
        conversation.typed("Count")
        self.told.until(sound)
        conversation.typed("Stop counting")
        rest.set()
        self.finish(conversation)
        self.assertEqual(self.told.shown(), [
            "thinking", "Count", "speaking", "One.", "One.", "hush", "thinking", "Stop counting",
            "speaking", "Fine.", "Fine.", "timing", "done", "listening",
        ])
        self.assertEqual([message["content"] for message in conversation.history], ["Count", "One.", "Stop counting", "Fine."])

    def test_thinking_is_never_said_or_remembered(self):
        conversation = self.conversation(Answers(["<thi", "nk>The tide. Six, I think.</th", "ink>At six", " o'clock."]))
        conversation.typed("When?")
        self.finish(conversation)
        self.assertEqual([said for said, _ in self.says.said], ["At six o'clock."])
        self.assertEqual(conversation.history[-1]["content"], "At six o'clock.")

    def test_what_cannot_be_said_is_stepped_over(self):
        conversation = self.conversation(Answers(["**Well.** 🙂. It is `six`."]))
        conversation.typed("When?")
        self.finish(conversation)
        self.assertEqual([said for said, _ in self.says.said], ["Well.", "It is six."])
        self.assertEqual([told["text"] for told in self.told.of("said")], ["Well.", "It is six."])

    def test_an_answer_of_nothing_ends_the_turn_quietly(self):
        conversation = self.conversation(Answers([]))
        conversation.typed("Hello?")
        self.finish(conversation)
        self.assertEqual(self.told.shown(), ["thinking", "Hello?", "timing", "done", "listening"])
        self.assertEqual(conversation.history, [{"role": "user", "content": "Hello?"}])

    def test_a_writer_that_only_writes_is_asked_for_the_whole_answer(self):
        class Writes:
            name, model = "stand-in", "made-up-writer"
            asked = []

            def write(self, prompt, *, limit=200, as_json=False):
                self.asked.append((prompt, limit))
                return "Out. It turns at six."

        writer = Writes()
        conversation = self.conversation(writer, system="You know the tides.", limit=90)
        conversation.typed("What is the tide doing?")
        self.finish(conversation)
        self.assertEqual([said for said, _ in self.says.said], ["Out.", "It turns at six."])
        ((prompt, limit),) = writer.asked
        self.assertEqual(limit, 90)
        self.assertTrue(prompt.startswith("You know the tides.\n\nThey said: What is the tide doing?"))

    def test_a_voice_that_fails_is_told_and_the_turn_ends(self):
        conversation = self.conversation(Answers(["Out."]), says=Says(SpeechError("the speech model has no voice 'ada'")))
        conversation.typed("Well?")
        self.finish(conversation)
        self.assertEqual(self.told.shown(), ["thinking", "Well?", "the speech model has no voice 'ada'", "done", "listening"])
        self.assertEqual(self.told.of("trouble"), [{"type": "trouble", "text": "the speech model has no voice 'ada'"}])
        # The next turn is taken as usual.
        self.assertEqual(conversation.history, [{"role": "user", "content": "Well?"}])

    def test_a_writer_that_fails_part_way_is_told_and_what_was_said_is_kept(self):
        conversation = self.conversation(Answers(["Out. ", "It ", TextError("the text model at made-up did not answer")]))
        conversation.typed("Well?")
        self.finish(conversation)
        self.assertEqual(self.told.shown()[-4:], ["Out.", "the text model at made-up did not answer", "done", "listening"])
        self.assertEqual(conversation.history[-1], {"role": "assistant", "content": "Out."})

    def test_a_fault_nobody_expected_is_told_by_its_name(self):
        conversation = self.conversation(Answers(["Out. ", KeyError("choices")]))
        conversation.typed("Well?")
        self.finish(conversation)
        self.assertEqual(self.told.of("trouble"), [{"type": "trouble", "text": "KeyError: 'choices'"}])

    def test_a_spoken_turn_is_written_down_and_then_answered(self):
        hears = Hears("What is the tide doing?")
        writer = Answers(["Out."])
        conversation = self.conversation(writer, hears=hears, language="cy")
        conversation.heard(b"\x01\x00" * 8000)
        self.finish(conversation)
        self.assertEqual(hears.played, [(16_000, 1, 2, 8000, "cy")])
        self.assertEqual(self.told.shown(), ["thinking", "What is the tide doing?", "speaking", "Out.", "Out.", "timing", "done", "listening"])
        self.assertIn("heard_ms", self.told.of("timing")[0])
        self.assertEqual(writer.asked[0][-1], {"role": "user", "content": "What is the tide doing?"})

    def test_a_noise_that_was_no_words_is_not_answered(self):
        for heard in ("", "[MUSIC]", "  "):
            with self.subTest(heard=heard):
                writer = Answers(["Out."])
                conversation = self.conversation(writer, hears=Hears(heard))
                conversation.heard(b"\x01\x00" * 8000)
                self.finish(conversation)
                self.assertEqual(self.told.shown(), ["thinking", "done", "listening"])
                self.assertEqual((writer.asked, conversation.history), ([], []))

    def test_no_sound_at_all_is_no_turn(self):
        conversation = self.conversation(Answers(), hears=Hears("anything"))
        conversation.heard(b"")
        conversation.typed("   ")
        self.assertEqual(self.told.all, [])

    def test_speech_cannot_be_taken_with_nothing_to_write_it_down(self):
        conversation = self.conversation(Answers())
        conversation.heard(b"\x01\x00" * 8000)
        self.finish(conversation)
        self.assertEqual(self.told.shown(), ["thinking", "nothing is listening here: this conversation can only be typed",
                                             "done", "listening"])

    def test_nothing_is_told_once_a_conversation_is_closed(self):
        rest = threading.Event()
        conversation = self.conversation(Answers(["One. ", rest, "Two."]))
        conversation.typed("Count")
        self.told.until(sound)
        conversation.close()
        told = list(self.told.all)
        rest.set()
        self.finish(conversation)
        conversation.hush()
        self.assertEqual(self.told.all, told)


class Scripted:
    """Ears that hear what they are told to: how likely speech is, frame by frame."""

    name = "scripted"

    def __init__(self, *likely):
        self.likely, self.resets = list(likely), 0

    def speech(self, frame):
        return self.likely.pop(0) if self.likely else 0.0

    def reset(self):
        self.resets += 1


def frames(first, count):
    """`count` frames of sound, every sample of each one its frame's number."""
    return b"".join(array.array("h", [number] * hearing.FRAME).tobytes() for number in range(first, first + count))


def numbered(said):
    """The numbers of the frames in something said."""
    held = hearing.samples(said)
    return [held[at] for at in range(0, len(held), hearing.FRAME)]


class UtterancesTest(unittest.TestCase):
    def test_what_was_said_comes_once_it_is_over_with_a_little_from_before_it(self):
        said = hearing.Utterances(Scripted(*[0.1] * 20, *[0.9] * 30, *[0.1] * 25))
        heard = []
        for at in range(75):
            for one in said.push(frames(at, 1)):
                heard.append((at, one))
        self.assertEqual([at for at, _ in heard], [27, 68])
        # A quarter of a second of speech is someone speaking: the eighth frame of it.
        self.assertIsNone(heard[0][1])
        # What came just before is kept, and it ends six tenths of a second after the last word.
        self.assertEqual(numbered(heard[1][1]), list(range(12, 69)))
        self.assertFalse(said.speaking)

    def test_a_knock_is_not_someone_speaking(self):
        said = hearing.Utterances(Scripted(*[0.1] * 5, *[0.9] * 7, *[0.1] * 40, *[0.9] * 7, *[0.1] * 40))
        self.assertEqual(list(said.push(frames(0, 99))), [])

    def test_a_breath_between_words_does_not_end_it(self):
        said = hearing.Utterances(Scripted(*[0.9] * 10, *[0.1] * 18, *[0.9] * 5, *[0.1] * 19))
        heard = list(said.push(frames(0, 52)))
        self.assertEqual([one is None for one in heard], [True, False])
        self.assertEqual(numbered(heard[1]), list(range(52)))

    def test_once_speaking_a_frame_a_little_under_the_mark_is_still_speech(self):
        said = hearing.Utterances(Scripted(*[0.9] * 8, *[0.4] * 60))
        self.assertEqual(list(said.push(frames(0, 68))), [None])
        self.assertTrue(said.speaking)
        # The same sound would not have started anything.
        self.assertEqual(list(hearing.Utterances(Scripted(*[0.4] * 60)).push(frames(0, 60))), [])

    def test_however_the_sound_arrives_the_same_is_heard(self):
        likely = [*[0.1] * 9, *[0.9] * 20, *[0.1] * 30]
        whole = list(hearing.Utterances(Scripted(*likely)).push(frames(0, 59)))
        self.assertEqual(len(whole), 2)
        for size in (1, 333, 1024, 5000):
            with self.subTest(size=size):
                said, sound_, heard = hearing.Utterances(Scripted(*likely)), frames(0, 59), []
                for at in range(0, len(sound_), size):
                    heard.extend(said.push(sound_[at:at + size]))
                self.assertEqual(heard, whole)

    def test_someone_who_does_not_stop_is_taken_a_piece_at_a_time(self):
        said = hearing.Utterances(Scripted(*[0.9] * 200), longest_s=1)
        heard = list(said.push(frames(0, 100)))
        # A second is 32 frames; and what goes on being said is another thing said.
        self.assertEqual([one is None for one in heard], [True, False, True, False, True, False])
        self.assertEqual([numbered(one) for one in heard[1::2]], [list(range(32)), list(range(32, 64)), list(range(64, 96))])
        self.assertTrue(said.speaking is False)

    def test_what_is_half_heard_can_be_dropped(self):
        ears = Scripted(*[0.9] * 10)
        said = hearing.Utterances(ears)
        self.assertEqual(list(said.push(frames(0, 10) + b"\x00" * 7)), [None])
        said.reset()
        self.assertFalse(said.speaking)
        self.assertEqual(ears.resets, 1)
        self.assertEqual(list(said.push(frames(0, 30))), [])


def tone(level, count=1):
    """Frames of a square wave this loud, out of 32768."""
    return [array.array("h", [level if at % 16 < 8 else -level for at in range(hearing.FRAME)]).tobytes()
            for _ in range(count)]


class LoudnessTest(unittest.TestCase):
    def test_samples_are_read_least_significant_byte_first(self):
        self.assertEqual(list(hearing.samples(b"\x01\x00\xff\xff\x00\x80")), [1, -1, -32768])

    def test_what_is_well_above_the_room_is_speech(self):
        ears = hearing.Loudness()
        self.assertEqual([ears.speech(frame) for frame in tone(60, 30)], [0.0] * 30)
        self.assertEqual([ears.speech(frame) for frame in tone(6000, 10)], [1.0] * 10)
        self.assertEqual([ears.speech(frame) for frame in tone(60, 10)], [0.0] * 10)

    def test_a_little_above_the_room_is_not(self):
        ears = hearing.Loudness()
        for frame in tone(300, 30):
            ears.speech(frame)
        # Twice as loud is six decibels, which is nothing; four times is as likely speech as not.
        self.assertEqual(ears.speech(tone(600)[0]), 0.0)
        self.assertAlmostEqual(hearing.Loudness.ABOVE, 12.0)
        ears.reset()
        for frame in tone(300, 30):
            ears.speech(frame)
        self.assertAlmostEqual(ears.speech(tone(1200)[0]), 0.5, places=1)

    def test_silence_and_the_very_quiet_are_never_speech(self):
        ears = hearing.Loudness()
        self.assertEqual(ears.speech(bytes(hearing.BYTES)), 0.0)
        self.assertEqual(ears.speech(b""), 0.0)
        # Forty times louder than silence's own noise, and still far too quiet to be a voice.
        self.assertEqual([ears.speech(frame) for frame in [*tone(1, 20), *tone(40, 5)]], [0.0] * 25)

    def test_a_room_that_got_louder_is_the_room_after_a_while(self):
        ears = hearing.Loudness()
        for frame in tone(100, 10):
            ears.speech(frame)
        heard = [ears.speech(frame) for frame in tone(1000, 400)]
        self.assertEqual(heard[0], 1.0)
        self.assertEqual(heard[-1], 0.0)

    def test_an_utterance_is_found_in_real_sound(self):
        said = hearing.Utterances(hearing.Loudness())
        heard = list(said.push(b"".join([*tone(40, 30), *tone(5000, 25), *tone(40, 30)])))
        self.assertEqual([one is None for one in heard], [True, False])
        self.assertEqual(len(heard[1]) // hearing.BYTES, 8 + 8 + 17 + 19)


class Session:
    """A stand-in for the model: it keeps what it was shown and answers what it was told to."""

    def __init__(self, *likely):
        self.likely, self.shown = list(likely), []

    def run(self, wanted, given):
        import numpy

        self.shown.append({name: numpy.array(value) for name, value in given.items()})
        return [numpy.array([[self.likely.pop(0)]], dtype=numpy.float32), given["state"] + 1]


@needs_numpy
class SileroTest(unittest.TestCase):
    def test_each_frame_is_shown_with_the_end_of_the_one_before_and_what_the_model_remembers(self):
        import numpy

        session = Session(0.25, 0.75)
        ears = hearing.Silero(session)
        first, second = frames(16384, 1), frames(-8192, 1)
        self.assertEqual([ears.speech(first), ears.speech(second)], [0.25, 0.75])
        one, two = session.shown
        self.assertEqual((one["input"].shape, one["input"].dtype, one["state"].shape, int(one["sr"])),
                         ((1, 576), numpy.float32, (2, 1, 128), 16_000))
        self.assertEqual(one["sr"].dtype, numpy.int64)
        self.assertTrue((one["input"][0, :64] == 0).all() and (one["input"][0, 64:] == 0.5).all())
        self.assertTrue((two["input"][0, :64] == 0.5).all() and (two["input"][0, 64:] == -0.25).all())
        self.assertTrue((one["state"] == 0).all() and (two["state"] == 1).all())

    def test_another_microphone_starts_from_nothing(self):
        session = Session(0.5, 0.5)
        ears = hearing.Silero(session)
        ears.speech(frames(16384, 1))
        ears.reset()
        ears.speech(frames(16384, 1))
        self.assertTrue((session.shown[1]["input"][0, :64] == 0).all() and (session.shown[1]["state"] == 0).all())


class EarsTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.settings = settings_in(folder.name)

    def installed(self):
        return mock.patch.dict(sys.modules, {name: types.ModuleType(name) for name in ("numpy", "onnxruntime")})

    def test_without_the_runtime_speech_is_told_by_loudness(self):
        for missing in ("numpy", "onnxruntime"):
            with self.subTest(missing=missing), self.installed(), mock.patch.dict(sys.modules, {missing: None}):
                self.assertIs(hearing.ears(self.settings), hearing.Loudness)

    def test_with_it_the_model_is_loaded_once_for_every_microphone(self):
        with self.installed(), mock.patch.object(hearing.Silero, "session", return_value="the model") as loaded, \
                mock.patch.object(hearing.Silero, "__init__", return_value=None) as made:
            ears = hearing.ears(self.settings)
            ears(), ears()
        loaded.assert_called_once_with(self.settings)
        self.assertEqual(made.call_args_list, [mock.call("the model")] * 2)

    def test_a_model_that_cannot_be_fetched_is_said_and_done_without(self):
        said = []
        with self.installed(), mock.patch.object(hearing.Silero, "session", side_effect=ModelError("could not download it.")):
            self.assertIs(hearing.ears(self.settings, tell=said.append), hearing.Loudness)
        self.assertEqual(said, ["could not download it. Until then speech is told from the room by how loud it is."])

    def test_the_model_is_one_published_file_pinned_to_what_it_must_be(self):
        self.assertRegex(hearing.SILERO.url, r"^https://huggingface\.co/[^/]+/[^/]+/resolve/[0-9a-f]{40}/")
        self.assertRegex(hearing.SILERO.sha256, r"^[0-9a-f]{64}$")
        with mock.patch.object(hearing.runtime, "available"), mock.patch.object(hearing.runtime, "session") as session, \
                mock.patch.object(hearing, "fetch", side_effect=lambda remote, target: target) as fetched:
            hearing.Silero.session(self.settings)
        fetched.assert_called_once_with(hearing.SILERO, self.settings.models_dir / "talk" / "silero-vad.onnx")
        session.assert_called_once_with(self.settings.models_dir / "talk" / "silero-vad.onnx", "cpu")


def raw(code, data=b"", *, last=True, masked=True):
    """One frame, written by hand."""
    head = bytes([(0x80 if last else 0) | code])
    size = len(data)
    if size < 126:
        head += bytes([(0x80 if masked else 0) | size])
    elif size < 1 << 16:
        head += bytes([(0x80 if masked else 0) | 126]) + struct.pack("!H", size)
    else:
        head += bytes([(0x80 if masked else 0) | 127]) + struct.pack("!Q", size)
    if not masked:
        return head + data
    key = b"\x11\x22\x33\x44"
    return head + key + bytes(byte ^ key[at % 4] for at, byte in enumerate(data))


class WebSocketTest(unittest.TestCase):
    def setUp(self):
        ours, theirs = socket.socketpair()
        self.wire = theirs
        files = [ours.makefile("rb"), ours.makefile("wb"), theirs.makefile("rb"), theirs.makefile("wb")]
        for held in (*files, ours, theirs):
            self.addCleanup(held.close)
        self.server = websocket.Socket(files[0], files[1])
        self.client = websocket.Socket(files[2], files[3], client=True)

    def test_the_answer_to_a_key_is_the_one_the_standard_gives(self):
        self.assertEqual(websocket.accepting("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_messages_arrive_as_they_were_sent_both_ways(self):
        for message in ("", "Hello", "tide — 潮", b"", b"\x00\x01\xff", "a" * 125, "b" * 126, b"c" * 65535, b"d" * 65536):
            with self.subTest(length=len(message), kind=type(message).__name__):
                self.assertEqual(self.passed(self.client, self.server, message), message)
                self.assertEqual(self.passed(self.server, self.client, message), message)

    def passed(self, sender, receiver, message):
        """What arrives of a message. It is read while it is written: a socket holds only so much."""
        sending = threading.Thread(target=sender.send, args=(message,))
        sending.start()
        try:
            return receiver.receive()
        finally:
            sending.join(5)

    def test_a_message_far_larger_than_a_socket_holds_arrives_whole(self):
        message = bytes(range(256)) * 8192
        self.assertEqual(self.passed(self.client, self.server, message), message)

    def test_a_ping_is_answered_and_is_no_message(self):
        self.wire.sendall(raw(websocket.PING, b"still there?") + raw(websocket.TEXT, b"yes"))
        self.assertEqual(self.server.receive(), "yes")
        self.assertEqual(self.client._read(), (True, websocket.PONG, b"still there?"))

    def test_a_message_sent_in_parts_is_one_message(self):
        self.wire.sendall(raw(websocket.TEXT, b"The tide ", last=False) + raw(websocket.PING)
                          + raw(websocket.CONTINUED, b"is going ", last=False) + raw(websocket.CONTINUED, b"out."))
        self.assertEqual(self.server.receive(), "The tide is going out.")

    def refused(self, sent, code):
        self.wire.sendall(sent)
        with self.assertRaises(websocket.Closed):
            self.server.receive()
        last, kind, data = self.client._read()
        self.assertEqual((kind, struct.unpack("!H", data)[0]), (websocket.CLOSE, code))
        with self.assertRaises(websocket.Closed):
            self.server.send("anything more")

    def test_a_client_that_does_not_mask_is_not_a_browser(self):
        self.refused(raw(websocket.TEXT, b"Hello", masked=False), 1002)

    def test_parts_out_of_order_are_refused(self):
        self.refused(raw(websocket.CONTINUED, b"out."), 1002)

    def test_a_new_message_inside_another_is_refused(self):
        self.refused(raw(websocket.TEXT, b"The ", last=False) + raw(websocket.TEXT, b"tide"), 1002)

    def test_an_extension_nobody_agreed_is_refused(self):
        self.refused(bytes([0xC1, 0x80]) + b"\x00" * 4, 1002)

    def test_a_message_too_large_to_take_is_refused_before_it_is_read(self):
        self.refused(bytes([0x82, 0xFF]) + struct.pack("!Q", websocket.LARGEST + 1), 1009)

    def test_text_that_is_not_text_is_refused(self):
        self.refused(raw(websocket.TEXT, b"\xff\xfe"), 1007)

    def test_goodbye_is_said_once_and_ends_it_for_both(self):
        self.client.close()
        self.client.close()
        with self.assertRaises(websocket.Closed):
            self.server.receive()
        with self.assertRaises(websocket.Closed):
            self.client.receive()
        with self.assertRaises(websocket.Closed):
            self.client.send("after")

    def test_an_end_that_vanishes_is_closed_too(self):
        self.wire.shutdown(socket.SHUT_RDWR)
        with self.assertRaises(websocket.Closed):
            self.server.receive()


class ServedTalk(unittest.TestCase):
    def serve(self, writer, *, hears=None, ears=None, **more):
        self.says = Says()
        parts = talk.Parts(writer=writer, synthesizer=self.says, voice=ADA, transcriber=hears)
        served = serve.Talk(parts, ears=ears, **more).start()
        self.addCleanup(served.stop)
        return served

    def open(self, served, **how):
        line = websocket.connect(served.socket, timeout=5, **how)
        self.addCleanup(line.close)
        return line

    def until(self, line, kind):
        """Everything told up to and including the first message of this kind."""
        told = []
        while True:
            message = line.receive()
            told.append(letters(message) if isinstance(message, bytes) else json.loads(message))
            if isinstance(told[-1], dict) and told[-1]["type"] == kind:
                return told


class ServeTest(ServedTalk):
    def get(self, served, path, **headers):
        connection = http.client.HTTPConnection("127.0.0.1", served.port, timeout=5)
        self.addCleanup(connection.close)
        connection.request("GET", path, headers=headers)
        answer = connection.getresponse()
        return answer.status, dict(answer.getheaders()), answer.read()

    def test_the_page_and_its_microphone_are_served(self):
        served = self.serve(Answers())
        status, headers, page = self.get(served, "/")
        self.assertEqual((status, headers["Content-Type"]), (200, "text/html; charset=utf-8"))
        self.assertIn(b"<title>Talk</title>", page)
        self.assertEqual(self.get(served, "/index.html")[2], page)
        status, headers, script = self.get(served, "/microphone.js?v=2")
        self.assertEqual((status, headers["Content-Type"]), (200, "text/javascript"))
        self.assertIn(b'registerProcessor("microphone"', script)
        self.assertEqual(self.get(served, "/pyproject.toml")[0], 404)
        self.assertEqual(self.get(served, "/../serve.py")[0], 404)

    def test_the_page_asks_for_nothing_from_anywhere_else(self):
        for name in ("index.html", "microphone.js"):
            page = (serve.PAGE / name).read_text()
            self.assertNotRegex(page, r"https?://")
            self.assertNotIn("@import", page)

    def test_it_listens_on_this_machine_only(self):
        served = self.serve(Answers())
        self.assertRegex(served.address, r"^http://127\.0\.0\.1:\d+$")
        self.assertEqual(served._server.server_address[0], "127.0.0.1")

    def test_a_name_that_was_made_to_point_here_is_turned_away(self):
        served = self.serve(Answers())
        for host in ("made-up.example", f"made-up.example:{served.port}", "127.0.0.1", f"127.0.0.1:{served.port + 1}"):
            with self.subTest(host=host):
                self.assertEqual(self.get(served, "/", Host=host)[0], 403)
        self.assertEqual(self.get(served, "/", Host=f"localhost:{served.port}")[0], 200)

    def test_another_site_s_page_cannot_open_a_conversation(self):
        writer = Answers(["Out."])
        served = self.serve(writer)
        for origin in ("http://made-up.example", f"https://127.0.0.1:{served.port}", "null"):
            with self.subTest(origin=origin), self.assertRaises(websocket.Closed) as refused:
                websocket.connect(served.socket, origin=origin, timeout=5)
            self.assertIn("403", str(refused.exception))
        for origin in (served.address, f"http://localhost:{served.port}"):
            self.assertEqual(json.loads(self.open(served, origin=origin).receive())["type"], "ready")
        self.assertEqual(writer.asked, [])

    def test_a_conversation_is_only_opened_as_a_websocket(self):
        served = self.serve(Answers())
        self.assertEqual(self.get(served, serve.WHERE)[0], 400)
        status, headers, _ = self.get(served, serve.WHERE, **{
            "Upgrade": "websocket", "Connection": "Upgrade", "Sec-WebSocket-Key": "bWFkZS11cA==", "Sec-WebSocket-Version": "8"})
        self.assertEqual((status, headers["Sec-WebSocket-Version"]), (426, "13"))
        with self.assertRaises(websocket.Closed) as refused:
            websocket.connect(f"ws://127.0.0.1:{served.port}/", timeout=5)
        self.assertIn("is not a WebSocket", str(refused.exception))

    def test_a_port_that_is_taken_is_said(self):
        served = self.serve(Answers())
        with self.assertRaises(talk.TalkError) as refused:
            serve.Talk(served.parts, port=served.port)
        self.assertIn(f"port {served.port} cannot be listened on", str(refused.exception))

    def test_a_typed_turn_is_answered_over_the_socket(self):
        served = self.serve(Answers(["The tide is ", "going out. It turns at six."]), about={"text": "stand-in made-up-writer"})
        line = self.open(served)
        self.assertEqual(self.until(line, "state"), [
            {"type": "ready", "text": "stand-in made-up-writer", "hears": False}, {"type": "state", "value": "listening"}])
        line.send("not json at all")
        line.send(json.dumps(["nor", "a", "message"]))
        line.send(json.dumps({"type": "text", "text": "What is the tide doing?"}))
        told = self.until(line, "done")
        self.assertEqual([one if isinstance(one, str) else one.get("text") or one.get("value") or one["type"] for one in told], [
            "thinking", "What is the tide doing?", "speaking",
            "The tide is going out.", "The tide is going out.", "It turns at six.", "It turns at six.", "timing", "done"])

    def test_each_conversation_is_its_own(self):
        writer = Answers(["Out."], ["At six."])
        served = self.serve(writer)
        first, second = self.open(served), self.open(served)
        for line, said in ((first, "What is the tide doing?"), (second, "When does it turn?")):
            line.send(json.dumps({"type": "text", "text": said}))
            self.until(line, "done")
        self.assertEqual([len(asked) for asked in writer.asked], [2, 2])

    def test_what_is_said_into_the_microphone_is_a_turn(self):
        hears = Hears("What is the tide doing?")
        served = self.serve(Answers(["Out."]), hears=hears, ears=lambda: Scripted(*[0.9] * 20, *[0.1] * 30))
        line = self.open(served)
        self.assertTrue(self.until(line, "state")[0]["hears"])
        sound_ = frames(0, 50)
        for at in range(0, len(sound_), 4096):
            line.send(sound_[at:at + 4096])
        told = self.until(line, "done")
        # Someone starting to speak is told at once; what they said, when they have stopped.
        self.assertEqual(told[0], {"type": "state", "value": "listening"})
        self.assertEqual([one for one in told if isinstance(one, dict) and one["type"] == "heard"],
                         [{"type": "heard", "text": "What is the tide doing?"}])
        self.assertIn("Out.", told)
        self.assertEqual(hears.played, [(16_000, 1, 2, 39 * hearing.FRAME, "en")])

    def test_speaking_over_an_answer_stops_it(self):
        rest = threading.Event()
        self.addCleanup(rest.set)
        served = self.serve(Answers(["One. ", rest, "Two."]), hears=Hears("Stop"), ears=lambda: Scripted(*[0.9] * 8))
        line = self.open(served)
        line.send(json.dumps({"type": "text", "text": "Count"}))
        while not isinstance(line.receive(), bytes):
            pass
        line.send(frames(0, 8))
        self.assertEqual(self.until(line, "state")[-2:], [{"type": "hush"}, {"type": "state", "value": "listening"}])

    def test_the_stop_button_stops_it_too(self):
        rest = threading.Event()
        self.addCleanup(rest.set)
        served = self.serve(Answers(["One. ", rest, "Two."]))
        line = self.open(served)
        line.send(json.dumps({"type": "text", "text": "Count"}))
        while not isinstance(line.receive(), bytes):
            pass
        line.send(json.dumps({"type": "hush"}))
        self.assertEqual(self.until(line, "hush")[-1], {"type": "hush"})

    def test_sound_is_let_go_by_when_nothing_is_listening(self):
        served = self.serve(Answers(["Out."]), ears=lambda: Scripted(*[0.9] * 50))
        line = self.open(served)
        self.assertFalse(self.until(line, "state")[0]["hears"])
        line.send(frames(0, 50))
        line.send(json.dumps({"type": "text", "text": "Well?"}))
        self.assertEqual([one["text"] for one in self.until(line, "done") if isinstance(one, dict) and one["type"] == "heard"],
                         ["Well?"])

    def test_stopping_lets_go_of_whoever_is_still_talking(self):
        served = self.serve(Answers())
        line = self.open(served)
        self.until(line, "state")
        served.stop()
        with self.assertRaises(websocket.Closed):
            line.receive()
        with self.assertRaises((urllib.error.URLError, OSError)):
            urllib.request.urlopen(served.address, timeout=2)

    def test_nothing_answers_at_an_address_nobody_serves(self):
        held = socket.socket()
        held.bind(("127.0.0.1", 0))
        port = held.getsockname()[1]
        held.close()
        with self.assertRaises(websocket.Closed) as refused:
            websocket.connect(f"ws://127.0.0.1:{port}/talk", timeout=2)
        self.assertIn("did not answer", str(refused.exception))
        with self.assertRaises(websocket.Closed):
            websocket.connect("http://127.0.0.1/talk")


class MeasureTest(unittest.TestCase):
    def parts(self, writer, *, says=None, hears=None):
        self.says = says or Says()
        return talk.Parts(writer=writer, synthesizer=self.says, voice=ADA, transcriber=hears)

    def test_each_part_is_timed_the_first_time_and_once_warm(self):
        writer = Answers(["Rivers carry ", "it there. And then"], ["Rivers carry it there. ", "And then it stays."])
        hears = Hears("Why is the sea salty?")
        told = measure.measure(self.parts(writer, hears=hears))
        self.assertEqual([(part["part"], part["adapter"], part["model"]) for part in told["parts"]], [
            ("hearing", "stand-in", "made-up-ears"), ("answering", "stand-in", "made-up-writer"),
            ("saying", "stand-in", "made-up-voice")])
        for part in told["parts"]:
            self.assertGreaterEqual(part["first_ms"], 0)
            self.assertGreaterEqual(part["ms"], 0)
        self.assertEqual(told["wait_ms"], sum(part["ms"] for part in told["parts"]))
        hearing_, answering, _ = told["parts"]
        self.assertEqual((hearing_["heard"], answering["said"]), ("Why is the sea salty?", "Rivers carry it there."))
        self.assertIsNotNone(answering["first_word_ms"])
        # What was listened to is the line the voice said, twice; what was asked is what was heard.
        self.assertEqual(len(hears.played), 2)
        self.assertEqual([asked[-1]["content"] for asked in writer.asked], ["Why is the sea salty?"] * 2)
        # The first sentence is all that is waited for: the answer is dropped there, and that sentence is said.
        self.assertEqual([said for said, _ in self.says.said], [measure.LINE, "Rivers carry it there."])
        self.assertEqual(writer.dropped, 2)

    def test_a_recording_of_your_own_is_what_is_written_down(self):
        with tempfile.TemporaryDirectory() as folder:
            recording = f"{folder}/asked.wav"
            with open(recording, "wb") as file:
                file.write(talk.wav(b"\x01\x00" * 4000, 8000))
            hears = Hears("When does the tide turn?")
            measure.measure(self.parts(Answers(["Six."], ["Six."]), hears=hears), recording)
        self.assertEqual(hears.played, [(8000, 1, 2, 4000, "en")] * 2)

    def test_with_nothing_listening_only_what_is_used_is_timed(self):
        writer = Answers(["Salt."], ["Salt."])
        told = measure.measure(self.parts(writer))
        self.assertEqual([part["part"] for part in told["parts"]], ["answering", "saying"])
        self.assertEqual(writer.asked[0][-1]["content"], measure.QUESTION)
        self.assertIsNotNone(told["wait_ms"])

    def test_a_part_that_fails_is_said_and_no_wait_is_given(self):
        writer = Answers([TextError("the text model at made-up did not answer")])
        told = measure.measure(self.parts(writer, hears=Hears("Why?")))
        answering = told["parts"][1]
        self.assertEqual(answering["trouble"], "the text model at made-up did not answer")
        self.assertNotIn("ms", answering)
        self.assertIsNone(told["wait_ms"])
        # The others are still timed.
        self.assertIn("ms", told["parts"][0])
        self.assertIn("ms", told["parts"][2])

    def test_a_voice_that_cannot_speak_leaves_nothing_to_listen_to(self):
        told = measure.measure(self.parts(Answers(["Salt."], ["Salt."]), says=Says(SpeechError("no voice")), hears=Hears("x")))
        hearing_, answering, saying = told["parts"]
        self.assertEqual(saying["trouble"], "no voice")
        self.assertIn("nothing to listen to", hearing_["trouble"])
        self.assertIn("ms", answering)
        self.assertIsNone(told["wait_ms"])

    def test_a_model_that_answers_nothing_is_a_failure(self):
        told = measure.measure(self.parts(Answers([], [])))
        self.assertEqual(told["parts"][0]["trouble"], "it answered nothing")


class CommandTest(unittest.TestCase):
    def models(self, writer, says=None, hears=None):
        self.says = says or Says()
        return (mock.patch("constructive_media.text.writer", return_value=writer),
                mock.patch("constructive_media.speech.synthesizer", return_value=self.says),
                mock.patch("constructive_media.transcription.transcriber", return_value=hears or Hears("Why is the sea salty?")))

    def run_with(self, models, *arguments):
        with models[0], models[1] as synthesizer, models[2] as transcriber:
            status, out, err = run("talk", *arguments)
        self.synthesizer, self.transcriber = synthesizer, transcriber
        return status, out, err

    def test_what_does_not_go_together_is_said(self):
        for arguments, said in (
            (("--voice", "ada", "--profile", "made-up"), "give one of them"),
            (("--emotion", "warm"), "--emotion goes with --voice"),
            (("--json",), "--json goes with --measure"),
        ):
            with self.subTest(arguments=arguments):
                status, _, err = run("talk", *arguments)
                self.assertEqual(status, 2)
                self.assertIn(said, err)

    def test_measuring_prints_each_part_and_the_wait(self):
        status, out, _ = self.run_with(self.models(Answers(["Rivers. More"], ["Rivers. More"])), "--measure", "--profile", "made-up")
        self.assertEqual(status, 0, out)
        for said in ("hearing", "answering", "saying", "made-up-ears", "made-up-writer", "made-up-voice",
                     "heard: Why is the sea salty?", "said: Rivers.", "its first word came in",
                     "from the end of your sentence to the first sound:"):
            self.assertIn(said, out)
        self.assertEqual(self.says.said[0][1], Voice("voice", profile="made-up"))

    def test_measuring_as_json_and_with_nothing_listening(self):
        status, out, _ = self.run_with(self.models(Answers(["Salt."], ["Salt."])), "--measure", "--json", "--typed")
        self.assertEqual(status, 0, out)
        told = json.loads(out)
        self.assertEqual([part["part"] for part in told["parts"]], ["answering", "saying"])
        self.transcriber.assert_not_called()

    def test_a_part_that_fails_is_a_failure_of_the_command(self):
        models = self.models(Answers([TextError("the text model at made-up did not answer")]))
        status, out, _ = self.run_with(models, "--measure")
        self.assertEqual(status, 1)
        self.assertIn("failed: the text model at made-up did not answer", out)
        self.assertIn("no wait can be given until every part works", out)
        models = self.models(Answers([TextError("the text model at made-up did not answer")]))
        status, out, _ = self.run_with(models, "--measure", "--json")
        self.assertEqual((status, json.loads(out)["wait_ms"]), (1, None))

    def test_a_recording_that_is_not_there_is_said(self):
        status, _, err = self.run_with(self.models(Answers()), "--measure", "no-such-recording.wav")
        self.assertEqual(status, 2)
        self.assertIn("no-such-recording.wav: no such file", err)

    def test_serving_says_where_and_how_it_listens(self):
        with mock.patch("time.sleep", side_effect=KeyboardInterrupt):
            status, out, _ = self.run_with(self.models(Answers()), "--typed", "--port", "0", "--profile", "made-up",
                                           "--system", "You know the tides.")
        self.assertEqual(status, 0)
        self.assertRegex(out, r"the voice is ready, in \d+\.\d s\nhttp://127\.0\.0\.1:\d+\nturns are typed; stop with Ctrl-C\n")
        # The voice was tried before anything was served.
        self.assertEqual(self.says.said, [("Ready.", Voice("voice", profile="made-up"))])

    def test_serving_with_a_microphone_says_what_tells_speech_from_the_room(self):
        with mock.patch("time.sleep", side_effect=KeyboardInterrupt), \
                mock.patch("constructive_media.talk.hearing.ears", return_value=hearing.Loudness):
            hears = Hears("Ready.")
            status, out, _ = self.run_with(self.models(Answers(), hears=hears), "--port", "0", "--language", "cy")
        self.assertEqual(status, 0)
        self.assertRegex(out, r"the voice is ready, in \d+\.\d s\nthe listening is ready, in \d+\.\d s\nhttp://127")
        self.assertIn("speech is told from the room by loudness; stop with Ctrl-C", out)
        # What the voice said first is what was listened to first: both are warm before anyone speaks.
        self.assertEqual([(rate, language) for rate, _, _, _, language in hears.played], [(24_000, "cy")])

    def test_a_model_that_cannot_listen_is_found_out_before_anything_is_served(self):
        from constructive_media.transcription import TranscriptionError

        models = self.models(Answers(), hears=Hears(TranscriptionError("whisper-cli is not installed")))
        status, out, err = self.run_with(models, "--port", "0")
        self.assertEqual(status, 1)
        self.assertNotIn("http://", out)
        self.assertEqual(err, "media: whisper-cli is not installed\n")

    def test_a_voice_that_cannot_speak_is_found_out_before_anything_is_served(self):
        models = self.models(Answers(), says=Says(SpeechError("the speech model has no voice 'made-up'")))
        status, out, err = self.run_with(models, "--typed", "--port", "0", "--profile", "made-up")
        self.assertEqual((status, out), (1, ""))
        self.assertEqual(err, "media: the speech model has no voice 'made-up'\n")

    def test_a_port_that_is_taken_is_said(self):
        held = socket.socket()
        self.addCleanup(held.close)
        held.bind(("127.0.0.1", 0))
        held.listen()
        status, _, err = self.run_with(self.models(Answers()), "--typed", "--port", str(held.getsockname()[1]))
        self.assertEqual(status, 1)
        self.assertIn("cannot be listened on", err)


if __name__ == "__main__":
    unittest.main()
