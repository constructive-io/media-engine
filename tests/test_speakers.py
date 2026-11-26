"""Speakers without a database: features, grouping, the model adapter, and telling voices apart.

No voice here is anyone's. The "people" are steady notes, and the model the
adapter runs is a few numbers built on the spot.
"""

import importlib.util
import math
import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from constructive_media import adapters
from constructive_media.adapters import PERMISSIVE
from constructive_media.models import ModelError

from .support import ByNote, needs_numpy, settings_in, spoken


def have(*modules):
    return all(importlib.util.find_spec(module) is not None for module in modules)


@needs_numpy
class FbankTest(unittest.TestCase):
    """The features every published speaker model was trained on."""

    def signal(self):
        import numpy as np

        time = np.arange(8000) / 16000.0
        return (0.2 * sum(np.sin(2 * np.pi * 97 * k * time + k * k) / k for k in range(1, 41))).astype(np.float32)

    def test_it_matches_the_reference_implementation(self):
        from constructive_media.speakers.fbank import fbank

        # What Kaldi's fbank (through torchaudio, 80 bands, 25 ms frames every
        # 10 ms, Hamming window, no dither) gives for this signal.
        reference = {(0, 0): 17.761, (0, 40): 20.763, (0, 79): 11.124, (10, 5): 19.214, (24, 20): 20.341,
                     (24, 60): 18.890, (47, 0): 17.699, (47, 33): 20.622, (47, 79): 11.559}
        features = fbank(self.signal())
        self.assertEqual(features.shape, (48, 80))
        self.assertEqual(str(features.dtype), "float32")
        for (frame, band), value in reference.items():
            with self.subTest(frame=frame, band=band):
                self.assertAlmostEqual(float(features[frame, band]), value, delta=0.002)

    def test_frames_are_counted_before_they_are_made(self):
        from constructive_media.speakers.fbank import fbank, frames_in

        self.assertEqual(frames_in(8000), 48)
        self.assertEqual(frames_in(400), 1)
        self.assertEqual(frames_in(399), 0)
        self.assertEqual(fbank(self.signal()[:399]).shape, (0, 80))

    def test_levelling_removes_what_the_microphone_adds_to_every_frame(self):
        import numpy as np

        from constructive_media.speakers.fbank import fbank, levelled

        level = levelled(fbank(self.signal()))
        self.assertLess(float(np.abs(level.mean(axis=0)).max()), 1e-4)
        # A louder copy of a sound is the same sound.
        louder = levelled(fbank(self.signal() * 2))
        self.assertLess(float(np.abs(level - louder).max()), 1e-3)


def bundles(sizes, spread=0.05, seed=3, dimensions=8):
    """Made-up points: one tight bundle of unit vectors per size, each along its own axis."""
    import numpy as np

    chance = random.Random(seed)
    points = []
    for axis, size in enumerate(sizes):
        for _ in range(size):
            point = np.array([chance.gauss(0, spread) for _ in range(dimensions)])
            point[axis] += 1.0
            points.append(point / np.linalg.norm(point))
    order = list(range(len(points)))
    chance.shuffle(order)
    truth = [axis for axis, size in enumerate(sizes) for _ in range(size)]
    return np.stack([points[n] for n in order]), [truth[n] for n in order]


def same_groups(labels, truth):
    return len(set(zip(labels, truth))) == len(set(labels)) == len(set(truth))


@needs_numpy
class WardTest(unittest.TestCase):
    def slow(self, points):
        """Ward's method the long way: try every pair, join the cheapest."""
        import numpy as np

        groups = {n: (points[n].astype(float), 1) for n in range(len(points))}
        costs = []
        while len(groups) > 1:
            best = None
            for a in groups:
                for b in groups:
                    if a < b:
                        (centre_a, size_a), (centre_b, size_b) = groups[a], groups[b]
                        cost = math.sqrt(2 * size_a * size_b / (size_a + size_b)
                                         * float(np.sum((centre_a - centre_b) ** 2)))
                        if best is None or cost < best[0]:
                            best = (cost, a, b)
            cost, a, b = best
            (centre_a, size_a), (centre_b, size_b) = groups.pop(a), groups.pop(b)
            groups[max(groups, default=0) + len(points) + a + b] = (
                (centre_a * size_a + centre_b * size_b) / (size_a + size_b), size_a + size_b)
            costs.append(cost)
        return costs

    def test_it_joins_as_the_long_way_does(self):
        from constructive_media.speakers.cluster import ward

        points, _ = bundles((9, 7, 5), spread=0.3)
        joins = ward(points)
        self.assertEqual(len(joins), len(points) - 1)
        self.assertEqual(joins[-1].size, len(points))
        for join, cost in zip(joins, self.slow(points)):
            self.assertAlmostEqual(join.cost, cost, places=9)

    def test_a_cut_gives_the_groups_asked_for_biggest_first(self):
        from constructive_media.speakers.cluster import cut, ward

        points, truth = bundles((6, 14, 9))
        labels = cut(ward(points), len(points), 3)
        self.assertTrue(same_groups(labels, truth))
        self.assertEqual([labels.count(label) for label in range(3)], [14, 9, 6])
        self.assertEqual(cut(ward(points), len(points), 1), [0] * len(points))

    def test_the_voices_are_counted_when_nobody_says_how_many(self):
        from constructive_media.speakers.cluster import voices

        points, truth = bundles((40, 30, 26))
        labels = voices(points)
        self.assertTrue(same_groups(labels, truth))
        self.assertEqual([labels.count(label) for label in range(3)], [40, 30, 26])

    def test_one_voice_is_one_voice(self):
        from constructive_media.speakers.cluster import voices

        points, _ = bundles((80,), spread=0.1)
        self.assertEqual(set(voices(points)), {0})
        self.assertEqual(voices(points[:1]), [0])
        self.assertEqual(voices(points[:0]), [])

    def test_a_handful_of_windows_is_not_a_voice_of_its_own(self):
        from constructive_media.speakers.cluster import voices

        points, truth = bundles((40, 30, 5))
        labels = voices(points)
        self.assertEqual(max(labels), 1)
        self.assertTrue(same_groups([label for label, who in zip(labels, truth) if who != 2],
                                    [who for who in truth if who != 2]))
        # With a lower bar they are one.
        self.assertTrue(same_groups(voices(points, least=4), truth))

    def test_how_alike_two_voices_may_be_is_a_setting(self):
        import numpy as np

        from constructive_media.speakers.cluster import voices

        # Two bundles a small angle apart: their centres are about 0.8 alike.
        points, truth = bundles((40, 40), spread=0.02)
        turn = np.eye(8)
        turn[1, 0], turn[1, 1] = 0.8, 0.6
        points = np.stack([point @ turn for point in points])
        points /= np.linalg.norm(points, axis=1, keepdims=True)
        self.assertEqual(set(voices(points)), {0})
        self.assertTrue(same_groups(voices(points, alike=0.9), truth))


@needs_numpy
class LayAndCutTest(unittest.TestCase):
    def test_windows_cover_long_segments_and_short_ones_are_remarks(self):
        from constructive_media.speakers.voices import lay

        windows, remarks = lay([(0.0, 3.0), (10.0, 10.6), (20.0, 20.2), (30.0, 31.5)])
        self.assertEqual(windows, [(0, 0.0, 1.5), (0, 0.75, 2.25), (0, 1.5, 3.0), (3, 30.0, 31.5)])
        self.assertEqual(remarks, [(1, 10.0, 10.6)])

    def cut(self, spans, pieces):
        """Cut spans from a made-up sound in which each sample is its own number."""
        import numpy as np

        from constructive_media.speakers import voices

        sound = (np.arange(20000) % 30000).astype("<i2").tobytes()
        handed = [sound[start:end] for start, end in zip(pieces, pieces[1:] + [len(sound)])]
        with mock.patch.object(voices.ffmpeg, "samples", return_value=iter(handed)):
            found = dict(voices.cuts("/made-up/numbers.wav", spans))
        return {index: [round(float(value) * 32768) for value in (samples[0], samples[-1])] + [len(samples)]
                for index, samples in found.items()}

    def test_each_span_gets_its_own_samples_however_the_sound_arrives(self):
        rate = 16000
        spans = [(0.5, 0.75), (0.0, 0.25), (0.6, 1.0), (0.1, 0.2)]
        wanted = {index: [round(start * rate), round(end * rate) - 1, round((end - start) * rate)]
                  for index, (start, end) in enumerate(spans)}
        for pieces in ([0], [0, 4096, 8192, 30000], [0, 1, 4001, 4002, 12345, 25001], list(range(0, 40000, 777))):
            with self.subTest(pieces=len(pieces)):
                self.assertEqual(self.cut(spans, pieces), wanted)

    def test_a_span_past_the_end_comes_back_short_or_empty(self):
        import numpy as np

        from constructive_media.speakers import voices

        sound = np.zeros(16000, dtype="<i2").tobytes()
        with mock.patch.object(voices.ffmpeg, "samples", return_value=iter([sound])):
            found = dict(voices.cuts("/made-up/second.wav", [(0.5, 2.0), (3.0, 4.0)]))
        self.assertEqual({index: len(samples) for index, samples in found.items()}, {0: 8000, 1: 0})


def conversation(lines):
    """Segments for `(who, seconds)` lines, and the sound of any stretch of them."""
    import numpy as np

    segments, sound, now = [], [], 0.0
    for who, seconds in lines:
        people = who if isinstance(who, tuple) else (who,)
        line = np.concatenate([spoken(person, seconds / len(people)) for person in people])
        segments.append((now, now + seconds))
        sound += [line, np.zeros(8000, dtype=np.float32)]
        now += seconds + 0.5
    whole = np.concatenate(sound)

    def sounds(spans):
        return ((index, whole[round(start * 16000):round(end * 16000)])
                for index, (_, start, end) in enumerate(spans[0] + spans[1]))

    return segments, sounds


@needs_numpy
class TellTest(unittest.TestCase):
    LINES = [("ada", 6.0), ("ben", 4.5), ("ada", 6.0), ("cy", 3.0), ("ben", 4.5), ("ada", 3.0),
             ("cy", 3.0), ("ben", 0.6), ("cy", 0.2), (("ada", "cy"), 0.8)]

    def tell(self, lines=None, **options):
        from constructive_media.speakers import voices

        segments, sounds = conversation(lines or self.LINES)
        spans = voices.lay(segments)
        options.setdefault("least", 4)
        return voices.tell(segments, sounds(spans), spans, ByNote(), **options)

    def test_each_line_goes_to_whoever_spoke_it(self):
        telling = self.tell()
        self.assertEqual([voice.label for voice in telling.voices], ["voice1", "voice2", "voice3"])
        ada, ben, cy = 0, 1, 2
        self.assertEqual(telling.spoken_by[:7], [ada, ben, ada, cy, ben, ada, cy])
        self.assertEqual([voice.segments for voice in telling.voices], [3, 3, 2])
        self.assertEqual(telling.voices[0].speech_s, 15.0)
        for voice in telling.voices:
            self.assertGreater(voice.cohesion, 0.99)
            self.assertAlmostEqual(sum(value * value for value in voice.centre), 1.0, places=5)

    def test_a_short_remark_goes_to_the_voice_it_is_clearly_most_like(self):
        telling = self.tell()
        self.assertEqual(telling.spoken_by[7], 1)

    def test_what_is_too_short_or_unclear_is_left_unknown(self):
        telling = self.tell()
        self.assertEqual(telling.spoken_by[8:], [None, None])
        self.assertEqual(telling.unknown, 2)

    def test_a_line_two_people_share_goes_to_whoever_has_more_of_it(self):
        telling = self.tell([("ada", 6.0), ("ben", 6.0), (("ada", "ada", "ada", "ben"), 6.0), ("ada", 3.0)])
        self.assertEqual(telling.spoken_by, [0, 1, 0, 0])
        self.assertEqual(telling.split, 1)

    def test_the_number_of_voices_can_be_given(self):
        telling = self.tell(voices=2)
        self.assertEqual(len(telling.voices), 2)
        self.assertEqual(len(self.tell(voices=1).voices), 1)

    def test_a_card_is_a_few_separate_moments_of_a_voice(self):
        from constructive_media.speakers.voices import BEST, WINDOW_S

        telling = self.tell([("ada", 60.0), ("ben", 9.0)])
        ada, ben = telling.voices
        self.assertEqual(len(ada.best), BEST)
        for earlier, later in zip(ada.best, ada.best[1:]):
            self.assertGreaterEqual(later - earlier, WINDOW_S)
        self.assertTrue(all(60.5 <= start <= 68.0 for start in ben.best))

    def test_a_voice_that_says_little_is_not_counted_unless_told_to(self):
        lines = [("ada", 30.0), ("ben", 30.0), ("cy", 3.0)]
        self.assertEqual(len(self.tell(lines, least=24).voices), 2)
        self.assertEqual(len(self.tell(lines, least=24, voices=3).voices), 3)

    def test_nothing_long_enough_to_listen_to_is_nobody(self):
        telling = self.tell([("ada", 0.2), ("ben", 0.3)])
        self.assertEqual((telling.voices, telling.spoken_by), ([], [None, None]))

    def test_whose_names_the_nearest_voice_that_is_near_enough(self):
        from constructive_media.speakers.voices import whose

        known = [("Ada", 1, [1.0, 0.0, 0.0]), ("Ben", 2, [0.0, 1.0, 0.0]), ("Other model", 3, [1.0, 0.0])]
        self.assertEqual(whose([0.8, 0.6, 0.0], known)[:2], ("Ada", 1))
        self.assertEqual(whose([0.6, 0.8, 0.0], known)[:2], ("Ben", 2))
        self.assertIsNone(whose([0.0, 0.0, 1.0], known))
        self.assertIsNone(whose([0.8, 0.6, 0.0], known, least=0.9))


def tiny_model(folder, features=80, width=4):
    """A speaker model small enough to write here: how much each band moves, mixed down to a few numbers."""
    import numpy as np
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    mix = np.array([[math.sin(1 + row * (column + 1)) for column in range(width)] for row in range(features)],
                   dtype=np.float32)
    graph = helper.make_graph(
        [helper.make_node("Mul", ["feats", "feats"], ["moved"]),
         helper.make_node("ReduceMean", ["moved"], ["mean"], axes=[1], keepdims=0),
         helper.make_node("MatMul", ["mean", "mix"], ["embs"])],
        "tiny-speaker",
        [helper.make_tensor_value_info("feats", TensorProto.FLOAT, ["B", "T", features])],
        [helper.make_tensor_value_info("embs", TensorProto.FLOAT, ["B", width])],
        [numpy_helper.from_array(mix, "mix")],
    )
    folder.mkdir(parents=True, exist_ok=True)
    onnx.save(helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)], ir_version=8),
              str(folder / "model.onnx"))
    return folder


class KnownModelsTest(unittest.TestCase):
    def test_the_slot_is_filled_by_a_permissive_adapter(self):
        shipped = [adapter for adapter in adapters.builtins() if adapter.slot == "speaker"]
        self.assertEqual([adapter.name for adapter in shipped], ["onnx"])
        self.assertIn(shipped[0].license, PERMISSIVE)

    def test_every_known_model_is_permissive_and_pinned(self):
        from constructive_media.speakers.onnx import DEFAULT_MODEL, KNOWN

        self.assertIn(DEFAULT_MODEL, KNOWN)
        for name, known in KNOWN.items():
            with self.subTest(model=name):
                self.assertIn(known.license, PERMISSIVE)
                self.assertRegex(known.weights.url, r"^https://.+/resolve/[0-9a-f]{40}/")
                self.assertRegex(known.weights.sha256, r"^[0-9a-f]{64}$")

    def test_the_model_comes_from_the_setting(self):
        from constructive_media.speakers.onnx import DEFAULT_MODEL, OnnxSpeaker

        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(OnnxSpeaker(settings=settings_in(folder)).model, DEFAULT_MODEL)
            chosen = settings_in(folder, models={"speaker": "resnet34"})
            self.assertEqual(OnnxSpeaker(settings=chosen).model, "resnet34")

    def test_a_folder_that_is_not_a_model_is_named(self):
        from constructive_media.speakers.onnx import OnnxSpeaker

        with tempfile.TemporaryDirectory() as folder:
            speaker = OnnxSpeaker(str(Path(folder) / "nothing-here"), settings=settings_in(folder))
            with self.assertRaisesRegex(ModelError, "nothing-here.*model.onnx"):
                speaker.weights()


@unittest.skipUnless(have("onnx", "onnxruntime", "numpy"), "onnx, onnxruntime and numpy are needed")
class OnnxSpeakerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from constructive_media.speakers.onnx import OnnxSpeaker

        folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(folder.cleanup)
        cls.root = Path(folder.name)
        cls.speaker = OnnxSpeaker(str(tiny_model(cls.root / "tiny")), "cpu", settings=settings_in(cls.root))

    def test_each_sound_becomes_one_row_at_unit_length(self):
        import numpy as np

        from constructive_media.speakers import Embedder

        self.assertIsInstance(self.speaker, Embedder)
        rows = self.speaker.embed([spoken("ada", 1.5), spoken("ben", 1.5), spoken("cy", 1.5)])
        self.assertEqual((rows.shape, str(rows.dtype)), ((3, 4), "float32"))
        for row in rows:
            self.assertAlmostEqual(float(np.linalg.norm(row)), 1.0, places=5)
        # Another stretch of one voice is nearer to it than any other voice is.
        again = self.speaker.embed([spoken("ada", 3.0)[8000:32000]])[0]
        self.assertGreater(float(rows[0] @ again), 0.999)
        self.assertLess(max(float(rows[0] @ rows[1]), float(rows[0] @ rows[2])), 0.97)

    def test_rows_come_back_in_the_order_given_whatever_the_lengths(self):
        import numpy as np

        sounds = [spoken("ada", 1.5), spoken("ben", 0.6), spoken("cy", 1.5), spoken("ben", 1.5),
                  spoken("ada", 0.6)] * 9
        together = self.speaker.embed(sounds)
        for index in (0, 1, 2, 4, 44):
            alone = self.speaker.embed([sounds[index]])[0]
            self.assertLess(float(np.abs(together[index] - alone).max()), 1e-5)
        self.assertEqual(self.speaker.embed([]).shape[0], 0)

    def test_a_louder_copy_of_a_voice_is_the_same_voice(self):
        quiet, loud = self.speaker.embed([spoken("ada", 1.5) * 0.2, spoken("ada", 1.5)])
        self.assertGreater(float(quiet @ loud), 0.9999)

    def test_too_little_sound_is_refused(self):
        with self.assertRaisesRegex(ValueError, "too little"):
            self.speaker.embed([spoken("ada", 0.05)])

    def test_a_model_of_another_shape_is_refused(self):
        from constructive_media.speakers.onnx import OnnxSpeaker

        other = OnnxSpeaker(str(tiny_model(self.root / "forty", features=40)), "cpu",
                            settings=settings_in(self.root))
        with self.assertRaisesRegex(ModelError, "frames by 80"):
            other.embed([spoken("ada", 1.5)])

    def test_the_adapter_is_reached_through_its_slot(self):
        from constructive_media import speakers
        from constructive_media.speakers.onnx import OnnxSpeaker

        registry = adapters.Registry()
        for adapter in adapters.builtins():
            registry.register(adapter, default=True)
        with mock.patch.object(adapters, "registry", return_value=registry), \
                mock.patch.dict("os.environ", {"CONSTRUCTIVE_MEDIA_HOME": str(self.root),
                                               "CONSTRUCTIVE_MEDIA_MODEL_SPEAKER": str(self.root / "tiny")}):
            found = speakers.embedder()
        self.assertIsInstance(found, OnnxSpeaker)
        self.assertEqual(found.model, str(self.root / "tiny"))
