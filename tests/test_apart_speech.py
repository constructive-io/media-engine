"""A speech model in a process of its own, with stand-ins for the model.

The other process is a real one: this file is what it loads its stand-in
from. Nothing here needs a model or a graphics processor.
"""

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from constructive_media import gpu, speech
from constructive_media.speech import apart

from .support import settings_in

ROOT = Path(__file__).resolve().parent.parent
#: A line the stand-ins never finish.
STUCK = "This line is never finished."


class Quick:
    """Says a line by writing down what it was asked, and which process it is."""

    name, model = "quick", "a-stand-in"

    def say(self, text, target, *, voice, seed=None):
        if text == STUCK:
            time.sleep(60)
        Path(target).write_text(json.dumps({"text": text, "persona": voice.persona, "emotion": voice.emotion,
                                            "said": voice.reference_text, "seed": seed, "process": os.getpid()}))
        return Path(target)

    def say_as(self, text, target, *, embedding, seed=None):
        Path(target).write_text(json.dumps({"text": text, "as": embedding, "seed": seed}))
        return Path(target)

    def embed(self, recording):
        return [0.25, float(len(str(recording)))]


class Noisy(Quick):
    """Prints while it works, as a model's loader does."""

    def say(self, text, target, *, voice, seed=None):
        print("fetching 12 files")
        print('{"done": true}')
        return super().say(text, target, voice=voice, seed=seed)


class Refusing(Quick):
    def say(self, text, target, *, voice, seed=None):
        raise speech.SpeechError("the model answered with nothing to hear")


class Dying(Quick):
    def say(self, text, target, *, voice, seed=None):
        os._exit(3)


class Unbuilt:
    def __init__(self):
        raise speech.SpeechError("the speech model could not be loaded: no such folder")


class Loading(Quick):
    """Has a model to load, and says when it did."""

    def loaded(self):
        (Path(os.environ["CONSTRUCTIVE_MEDIA_HOME"]) / "loaded").write_text("yes")


class ApartTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.voice = {"persona": "ada", "emotion": "warm", "reference_text": "The lantern is lit at dusk."}

    def worker(self, stand_in, patience=30.0, **how):
        path = os.pathsep.join([str(ROOT), str(ROOT / "python"), os.environ.get("PYTHONPATH", "")])
        made = apart.Apart(f"tests.test_apart_speech:{stand_in}", patience=patience, env={
            **os.environ, "PYTHONPATH": path, "CONSTRUCTIVE_MEDIA_HOME": str(self.folder / "home")}, **how)
        self.addCleanup(made.close)
        return made

    def said(self, name):
        return json.loads((self.folder / name).read_text())

    def test_a_line_is_said_by_another_process_in_the_voice_it_was_given(self):
        worker = self.worker("Quick")
        worker.call("say", text="Bring the boats in.", target=str(self.folder / "one.txt"), voice=self.voice, seed=507)
        worker.call("say", text="And tie them.", target=str(self.folder / "two.txt"), voice=self.voice, seed=None)
        one, two = self.said("one.txt"), self.said("two.txt")
        self.assertEqual((one["text"], one["persona"], one["emotion"], one["said"], one["seed"]),
                         ("Bring the boats in.", "ada", "warm", "The lantern is lit at dusk.", 507))
        self.assertNotEqual(one["process"], os.getpid())
        self.assertEqual(one["process"], two["process"])     # the model is loaded once, and kept
        self.assertEqual(worker.stopped, 0)

    def test_a_line_that_takes_too_long_is_stopped_and_the_next_is_said_by_a_new_process(self):
        worker = self.worker("Quick", patience=2.0)
        worker.call("say", text="Bring the boats in.", target=str(self.folder / "one.txt"), voice=self.voice)
        began = time.monotonic()
        with self.assertRaisesRegex(speech.SpeechError, "still saying a line after 2 seconds"):
            worker.call("say", text=STUCK, target=str(self.folder / "stuck.txt"), voice=self.voice)
        self.assertLess(time.monotonic() - began, 20)
        self.assertFalse((self.folder / "stuck.txt").exists())
        worker.call("say", text="And tie them.", target=str(self.folder / "two.txt"), voice=self.voice)
        self.assertNotEqual(self.said("one.txt")["process"], self.said("two.txt")["process"])
        self.assertEqual(worker.stopped, 1)

    def test_waiting_a_turn_on_the_graphics_processor_is_not_taking_too_long(self):
        worker = self.worker("Quick", patience=2.0)
        worker.call("say", text="Bring the boats in.", target=str(self.folder / "one.txt"), voice=self.voice)
        done = []

        def ask():
            worker.call("say", text="And tie them.", target=str(self.folder / "two.txt"), voice=self.voice)
            done.append(time.monotonic())

        with gpu.alone(settings_in(self.folder / "home")):
            asking = threading.Thread(target=ask)
            asking.start()
            time.sleep(3.5)                                   # longer than a line is given
            self.assertEqual(done, [])
        asking.join(timeout=20)
        self.assertEqual((len(done), worker.stopped), (1, 0))
        self.assertEqual(self.said("two.txt")["text"], "And tie them.")

    def test_what_a_model_prints_is_not_taken_for_an_answer(self):
        worker = self.worker("Noisy")
        for number in range(3):
            worker.call("say", text=f"Line {number}.", target=str(self.folder / f"{number}.txt"), voice=self.voice)
        self.assertEqual([self.said(f"{number}.txt")["text"] for number in range(3)], ["Line 0.", "Line 1.", "Line 2."])

    def test_a_line_the_model_refuses_is_an_error_and_the_process_goes_on(self):
        worker = self.worker("Refusing")
        for _ in range(2):
            with self.assertRaisesRegex(speech.SpeechError, "nothing to hear"):
                worker.call("say", text="Bring the boats in.", target=str(self.folder / "one.txt"), voice=self.voice)
        self.assertEqual(worker.stopped, 0)

    def test_a_process_that_dies_is_said_to_have_and_is_started_again(self):
        worker = self.worker("Dying")
        for _ in range(2):
            with self.assertRaisesRegex(speech.SpeechError, r"process stopped \(3\)"):
                worker.call("say", text="Bring the boats in.", target=str(self.folder / "one.txt"), voice=self.voice)

    def test_a_model_that_cannot_be_loaded_says_why(self):
        with self.assertRaisesRegex(speech.SpeechError, "no such folder"):
            self.worker("Unbuilt").call("say", text="Bring the boats in.", target="one.txt", voice=self.voice)

    def test_the_model_is_loaded_before_the_first_line_is_timed(self):
        worker = self.worker("Loading")
        worker.call("say", text="Bring the boats in.", target=str(self.folder / "one.txt"), voice=self.voice)
        self.assertEqual((self.folder / "home" / "loaded").read_text(), "yes")

    def test_a_voice_is_heard_as_numbers_and_said_from_them(self):
        worker = self.worker("Quick")
        heard = worker.call("embed", recording="ada.wav")
        self.assertEqual(heard, [0.25, 7.0])
        worker.call("say_as", text="Bring the boats in.", target=str(self.folder / "as.txt"), embedding=heard, seed=3)
        self.assertEqual(self.said("as.txt"), {"text": "Bring the boats in.", "as": [0.25, 7.0], "seed": 3})

    def test_only_what_a_synthesizer_does_is_asked_and_a_line_is_given_some_time(self):
        with self.assertRaisesRegex(speech.SpeechError, "is not asked to 'close'"):
            self.worker("Quick").call("close")
        with self.assertRaisesRegex(speech.SpeechError, "more than none"):
            apart.Apart("tests.test_apart_speech:Quick", patience=0)

    def test_a_voice_is_made_from_what_was_sent_of_it(self):
        voice = apart.voice_from({**self.voice, "seed": 9, "something_new": True})
        self.assertEqual(voice, speech.Voice("ada", "warm", reference_text="The lantern is lit at dusk.", seed=9))


class LockTest(unittest.TestCase):
    def test_whatever_holds_the_lock_may_ask_for_it_again_and_nothing_else_may(self):
        with tempfile.TemporaryDirectory() as home:
            settings, order = settings_in(Path(home)), []

            def other():
                with gpu.alone(settings):
                    order.append("other")

            with gpu.alone(settings):
                with gpu.alone(settings):                     # would wait on itself for ever
                    order.append("again")
                waiting = threading.Thread(target=other)
                waiting.start()
                time.sleep(0.4)
                order.append("first")
            waiting.join(timeout=10)
            self.assertEqual(order, ["again", "first", "other"])
            with gpu.alone(settings):                         # and it was let go of
                pass


if __name__ == "__main__":
    unittest.main()
