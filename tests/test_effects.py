import math
import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from constructive_media import effects
from constructive_media.effects import EffectBackend, EffectError, Effects
from constructive_media.effects.ffmpeg import SPACES, FfmpegEffects

RATE = 16_000


def write_tone(path, seconds=0.5, hz=440.0):
    """A short sine tone, made here so the repository holds no audio."""
    frames = b"".join(
        struct.pack("<h", int(12_000 * math.sin(2 * math.pi * hz * n / RATE)))
        for n in range(int(seconds * RATE))
    )
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(frames)
    return path


def read(path):
    with wave.open(str(path), "rb") as audio:
        return audio.getnframes(), audio.readframes(audio.getnframes())


class EffectsTest(unittest.TestCase):
    def test_nothing_asked_is_falsy(self):
        self.assertFalse(Effects())
        self.assertTrue(Effects(compress=True))
        self.assertTrue(Effects(gain=0))

    def test_values_outside_the_format_ranges_are_errors(self):
        bad = [
            dict(reverb="hall", reverb_mix=1.5),
            dict(delay=0.001),
            dict(delay=6),
            dict(delay=0.2, delay_feedback=0.99),
            dict(delay=0.2, delay_mix=-0.1),
            dict(gain=30),
            dict(gain=-61),
        ]
        for values in bad:
            with self.subTest(values=values), self.assertRaises(EffectError):
                Effects(**values)

    def test_a_modifier_needs_what_it_modifies(self):
        with self.assertRaises(EffectError):
            Effects(reverb_mix=0.2)
        with self.assertRaises(EffectError):
            Effects(delay_feedback=0.2)
        with self.assertRaises(EffectError):
            Effects(delay_mix=0.2)

    def test_overrides_are_read_in_the_format_spelling(self):
        asked = Effects.from_overrides({
            "reverb": "Hall",
            "reverb-mix": "0.25",
            "delay": 0.3,
            "delay-feedback": "0.4",
            "compress": "yes",
            "gain": "-3",
            "voice": "someone-else's key",
        })
        self.assertEqual(
            asked,
            Effects(reverb="hall", reverb_mix=0.25, delay=0.3, delay_feedback=0.4,
                    compress=True, gain=-3.0),
        )

    def test_overrides_are_read_as_a_parsed_script_spells_them(self):
        asked = Effects.from_overrides({"reverb": "room", "reverb_mix": 0.2, "delay": 0.3,
                                        "delay_feedback": 0.4, "delay_mix": 0.1, "compress": True})
        self.assertEqual(asked, Effects(reverb="room", reverb_mix=0.2, delay=0.3, delay_feedback=0.4,
                                        delay_mix=0.1, compress=True))

    def test_overrides_with_bad_values_are_errors(self):
        for overrides in ({"gain": "loud"}, {"compress": "maybe"}, {"delay": True}):
            with self.subTest(overrides=overrides), self.assertRaises(EffectError):
                Effects.from_overrides(overrides)


class FilterTest(unittest.TestCase):
    def setUp(self):
        self.backend = FfmpegEffects()

    def test_it_is_an_effect_backend(self):
        self.assertIsInstance(self.backend, EffectBackend)

    def test_no_effects_no_filters(self):
        self.assertEqual(self.backend.filters(Effects()), [])

    def test_every_format_preset_is_offered(self):
        self.assertEqual(set(SPACES), set(effects.REVERB_PRESETS))

    def test_bigger_spaces_have_more_and_longer_echoes(self):
        room = self.backend.filters(Effects(reverb="room"))
        cathedral = self.backend.filters(Effects(reverb="cathedral"))
        self.assertEqual(room, ["aecho=0.8:0.7:35:0.28"])
        self.assertEqual(cathedral, ["aecho=0.8:0.7:90|150|230:0.42|0.3|0.18"])

    def test_reverb_mix_scales_the_echoes(self):
        self.assertEqual(
            self.backend.filters(Effects(reverb="room", reverb_mix=0.06)),
            ["aecho=0.8:0.7:35:0.14"],
        )
        self.assertEqual(self.backend.filters(Effects(reverb="room", reverb_mix=0)), ["anull"])
        self.assertEqual(
            self.backend.filters(Effects(reverb="room", reverb_mix=1)),
            ["aecho=0.8:0.7:35:1"],
        )

    def test_unknown_reverb_is_an_error_naming_the_presets(self):
        with self.assertRaises(EffectError) as raised:
            self.backend.filters(Effects(reverb="stadium"))
        self.assertIn("cathedral", str(raised.exception))

    def test_delay_repeats_fade_by_the_feedback(self):
        self.assertEqual(
            self.backend.filters(Effects(delay=0.25, delay_feedback=0.5, delay_mix=0.4)),
            ["aecho=0.8:0.7:250|500|750|1000:0.4|0.2|0.1|0.05"],
        )

    def test_delay_without_feedback_is_one_repeat(self):
        self.assertEqual(
            self.backend.filters(Effects(delay=0.25, delay_feedback=0, delay_mix=0.4)),
            ["aecho=0.8:0.7:250:0.4"],
        )

    def test_long_delays_stay_inside_what_ffmpeg_accepts(self):
        (chain,) = self.backend.filters(Effects(delay=5, delay_feedback=0.95, delay_mix=1))
        delays = [int(d) for d in chain.split(":")[2].split("|")]
        self.assertEqual(len(delays), 6)
        self.assertLessEqual(max(delays), 90_000)

    def test_filters_run_in_a_fixed_order(self):
        chain = self.backend.filters(
            Effects(reverb="plate", delay=0.2, compress=True, gain=-6)
        )
        self.assertEqual([f.split("=")[0] for f in chain], ["aecho", "aecho", "acompressor", "volume"])
        self.assertEqual(chain[-1], "volume=-6dB")


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.here = Path(self.directory.name)
        self.source = write_tone(self.here / "tone.wav")

    def test_no_effects_copies_the_audio_through(self):
        target = FfmpegEffects(binary="not-installed").apply(
            self.source, self.here / "out.wav", Effects()
        )
        self.assertEqual(read(target), read(self.source))

    def test_a_missing_ffmpeg_is_explained(self):
        with self.assertRaises(RuntimeError) as raised:
            FfmpegEffects(binary="not-installed").apply(
                self.source, self.here / "out.wav", Effects(gain=-3)
            )
        self.assertIn("install ffmpeg", str(raised.exception))

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is not installed")
    def test_gain_makes_it_quieter(self):
        target = effects.apply(self.source, self.here / "out.wav", Effects(gain=-12))
        frames, quiet = read(target)
        _, loud = read(self.source)
        self.assertEqual(frames, int(0.5 * RATE))
        peak = lambda raw: max(abs(s) for (s,) in struct.iter_unpack("<h", raw))
        self.assertAlmostEqual(peak(quiet) / peak(loud), 10 ** (-12 / 20), delta=0.02)

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is not installed")
    def test_floats_stay_floats_so_sound_past_full_scale_is_not_clipped(self):
        loud = [1.5 * math.sin(2 * math.pi * 440 * n / RATE) for n in range(RATE // 2)]
        data = struct.pack(f"<{len(loud)}f", *loud)
        source = self.here / "loud.wav"
        source.write_bytes(struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(data), b"WAVE", b"fmt ", 16,
                                       3, 1, RATE, RATE * 4, 4, 32, b"data", len(data)) + data)
        target = effects.apply(source, self.here / "out.wav", Effects(gain=-6))
        raw = target.read_bytes()
        self.assertEqual(struct.unpack_from("<H", raw, 20)[0], 3, "the answer is a WAV file of floats")
        samples = [s for (s,) in struct.iter_unpack("<f", raw[raw.index(b"data") + 8:])]
        # Clipped to full scale first, it would have come out at a half.
        self.assertAlmostEqual(max(samples), 1.5 * 10 ** (-6 / 20), delta=0.01)

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is not installed")
    def test_reverb_changes_the_audio_and_can_write_in_place(self):
        before = read(self.source)
        target = effects.apply(self.source, self.source, Effects(reverb="hall", compress=True))
        self.assertEqual(target, self.source)
        self.assertNotEqual(read(self.source)[1], before[1])
        self.assertEqual([p.name for p in self.here.iterdir()], ["tone.wav"])

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is not installed")
    def test_ffmpeg_failure_is_reported_and_leaves_nothing_behind(self):
        broken = self.here / "broken.wav"
        broken.write_bytes(b"not audio")
        with self.assertRaises(RuntimeError) as raised:
            effects.apply(broken, self.here / "out.wav", Effects(gain=-3))
        self.assertIn("ffmpeg failed", str(raised.exception))
        self.assertEqual(sorted(p.name for p in self.here.iterdir()), ["broken.wav", "tone.wav"])


if __name__ == "__main__":
    unittest.main()
