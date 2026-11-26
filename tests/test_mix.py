"""Sound as numbers, and the mix: levels, loops, fades, ducking, and stems that add up.

Every sound here is a tone or noise made on the spot.
"""

import math
import tempfile
import unittest
from pathlib import Path

from constructive_media import conv, ffmpeg
from constructive_media.effects import Effects

from .support import HAVE_NUMPY, needs_ffmpeg, needs_numpy

if HAVE_NUMPY:
    import numpy as np

    from constructive_media.audio import mix, pcm

RATE = 48_000
FIXTURES = Path(__file__).parent / "fixtures" / "conv-spec" / "scenes"


def tone(seconds=1.0, hz=440.0, loud=0.5, rate=RATE):
    time = np.arange(round(seconds * rate)) / rate
    return (loud * np.sin(2 * np.pi * hz * time)).astype(np.float32)


def noise(seconds=1.0, loud=0.1, seed=1, rate=RATE):
    return (np.random.default_rng(seed).standard_normal(round(seconds * rate)) * loud).astype(np.float32)


def at(samples, hz, rate):
    """How loud a sound is at one pitch, in dB against a full-scale tone."""
    window = np.hanning(len(samples))
    spectrum = np.abs(np.fft.rfft(samples * window)) / (window.sum() / 2)
    pitches = np.fft.rfftfreq(len(samples), 1 / rate)
    return 20 * math.log10(spectrum[(pitches > hz - 50) & (pitches < hz + 50)].max() + 1e-12)


class InAFolder(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.here = Path(folder.name)


@needs_numpy
@needs_ffmpeg
class Files(InAFolder):
    def test_what_is_written_is_what_is_read(self):
        sound = tone()
        for bits, close in ((16, 1e-4), (24, 1e-6), (32, 0.0)):
            with self.subTest(bits=bits):
                path = pcm.write(self.here / f"{bits}.wav", sound, RATE, bits=bits)
                back = pcm.read(path, rate=RATE, stereo=False)
                self.assertEqual(len(back), len(sound))
                self.assertLessEqual(float(np.abs(back - sound).max()), close)

    def test_floats_keep_what_passes_full_scale_and_whole_numbers_hold_at_it(self):
        loud = np.array([2.0, -2.0, 0.5], dtype=np.float32)
        floats = pcm.read(pcm.write(self.here / "f.wav", loud, 8000, bits=32), rate=8000, stereo=False)
        whole = pcm.read(pcm.write(self.here / "w.wav", loud, 8000, bits=16), rate=8000, stereo=False)
        self.assertEqual(floats.tolist(), [2.0, -2.0, 0.5])
        self.assertAlmostEqual(float(whole[0]), 1.0, places=3)
        self.assertAlmostEqual(float(whole[1]), -1.0, places=3)

    def test_a_wav_is_written_in_sixteen_twenty_four_or_thirty_two_bits(self):
        with self.assertRaises(ValueError):
            pcm.write(self.here / "no.wav", tone(), RATE, bits=8)

    def test_one_channel_read_as_two_is_as_loud_on_both_sides(self):
        path = pcm.write(self.here / "one.wav", tone(), RATE)
        both = pcm.read(path, rate=RATE)
        self.assertEqual(both.shape, (RATE, 2))
        self.assertAlmostEqual(float(np.abs(both[:, 0]).max()), 0.5, places=4)
        self.assertTrue(np.array_equal(both[:, 0], both[:, 1]))

    def test_two_channels_are_kept_apart_or_averaged_into_one(self):
        sides = np.stack([tone(), np.zeros(RATE, dtype=np.float32)], axis=1)
        path = pcm.write(self.here / "two.wav", sides, RATE)
        self.assertEqual(pcm.channels(path), 2)
        both = pcm.read(path, rate=RATE)
        self.assertAlmostEqual(float(np.abs(both[:, 0]).max()), 0.5, places=4)
        self.assertEqual(float(np.abs(both[:, 1]).max()), 0.0)
        self.assertAlmostEqual(float(np.abs(pcm.read(path, rate=RATE, stereo=False)).max()), 0.25, places=4)

    def test_sound_is_brought_to_the_rate_asked_for(self):
        path = pcm.write(self.here / "low.wav", tone(2.0, rate=24_000), 24_000)
        up = pcm.read(path, rate=RATE, stereo=False)
        self.assertAlmostEqual(len(up) / RATE, 2.0, delta=0.01)
        self.assertAlmostEqual(at(up[4800:-4800], 440, RATE), 20 * math.log10(0.5), delta=0.1)

    def test_sound_is_read_at_its_own_rate_when_none_is_asked_for(self):
        sound = tone(1.0, rate=22_050)
        path = pcm.write(self.here / "own.wav", sound, 22_050, bits=32)
        self.assertEqual(pcm.shape(path), (1, 22_050))
        self.assertEqual(pcm.rate(path), 22_050)
        self.assertTrue(np.array_equal(pcm.read(path, stereo=False), sound))

    def test_bringing_a_rate_up_leaves_no_image_of_the_top_of_the_band(self):
        # A tone 500 Hz under the top of a 24 kHz recording has a mirror image
        # 500 Hz over it once the rate is doubled, unless the filter is steep.
        path = pcm.write(self.here / "high.wav", tone(4.0, hz=11_500, loud=0.25, rate=24_000), 24_000, bits=32)
        up = pcm.read(path, rate=RATE, stereo=False)[4800:-4800]
        self.assertGreater(at(up, 11_500, RATE) - at(up, 12_500, RATE), 55)

    def test_how_long_a_file_runs(self):
        path = pcm.write(self.here / "long.wav", tone(1.5), RATE)
        self.assertAlmostEqual(pcm.seconds(path), 1.5, places=2)
        self.assertIsNone(pcm.seconds(self.here / "missing.wav"))

    def test_a_file_with_no_sound_is_an_error(self):
        (self.here / "words.wav").write_text("not sound")
        with self.assertRaises(ffmpeg.FfmpegError):
            pcm.read(self.here / "words.wav", rate=RATE)
        with self.assertRaises(ffmpeg.FfmpegError):
            pcm.rate(self.here / "words.wav")


@needs_numpy
class Levels(unittest.TestCase):
    def test_a_clip_is_brought_to_its_level(self):
        for target in (-34.0, -24.0, -20.0):
            with self.subTest(target=target):
                self.assertAlmostEqual(mix.loudness(mix.level(noise(), target)), target, places=2)

    def test_a_clip_that_is_almost_nothing_is_not_turned_up_into_its_noise(self):
        faint = noise(loud=1e-5)
        self.assertAlmostEqual(mix.loudness(mix.level(faint, -20.0)) - mix.loudness(faint), 24.0, places=2)

    def test_a_sharp_moment_is_turned_down_whole_and_never_clipped(self):
        smash = np.zeros(RATE, dtype=np.float32)
        smash[1000:1100] = 0.5
        out = mix.level(smash, -20.0)
        self.assertAlmostEqual(float(np.abs(out).max()), mix.CEILING, places=5)
        # The same gain everywhere: its shape is what it was.
        self.assertTrue(np.allclose(out / out.max(), smash / smash.max(), atol=1e-6))

    def test_nothing_stays_nothing(self):
        self.assertEqual(len(mix.level(np.zeros(0, dtype=np.float32), -20.0)), 0)
        self.assertEqual(mix.loudness(np.zeros(0)), -120.0)


@needs_numpy
class Lengths(unittest.TestCase):
    def test_a_long_clip_is_cut_and_a_short_one_padded_with_quiet(self):
        sound = tone(1.0)
        self.assertTrue(np.array_equal(mix.fit(sound, 0.25), sound[:RATE // 4]))
        padded = mix.fit(sound, 1.5)
        self.assertEqual(len(padded), RATE * 3 // 2)
        self.assertTrue(np.array_equal(padded[:RATE], sound))
        self.assertFalse(padded[RATE:].any())

    def test_a_loop_runs_for_exactly_as_long_as_asked(self):
        for seconds in (2.0, 3.7, 10.0001):
            for xfade in (0.0, 0.2):
                with self.subTest(seconds=seconds, xfade=xfade):
                    looped = mix.fit(noise(), seconds, loop=True, xfade=xfade)
                    self.assertEqual(len(looped), round(seconds * RATE))

    def test_a_plain_loop_is_the_clip_again_and_again(self):
        sound = noise(0.5)
        looped = mix.fit(sound, 1.25, loop=True)
        self.assertTrue(np.array_equal(looped[:len(sound)], sound))
        self.assertTrue(np.array_equal(looped[len(sound):2 * len(sound)], sound))

    def test_a_crossfaded_loop_has_no_seam(self):
        # A tone that does not come round to where it began: butted against
        # itself it jumps, and faded over itself it does not.
        sound = tone(1.003, hz=100.0)
        jump = lambda samples: float(np.abs(np.diff(samples)).max())
        self.assertGreater(jump(mix.fit(sound, 4.0, loop=True)), 0.1)
        self.assertLess(jump(mix.fit(sound, 4.0, loop=True, xfade=0.25)), 0.02)

    def test_a_loop_keeps_two_channels(self):
        both = np.stack([noise(), noise(seed=2)], axis=1)
        self.assertEqual(mix.fit(both, 2.5, loop=True, xfade=0.1).shape, (round(2.5 * RATE), 2))
        self.assertEqual(mix.fit(both, 2.5).shape, (round(2.5 * RATE), 2))

    def test_no_time_is_no_sound(self):
        self.assertEqual(len(mix.fit(tone(), 0)), 0)
        self.assertEqual(len(mix.fit(np.zeros(0, dtype=np.float32), 1.0)), RATE)


@needs_numpy
class Fades(unittest.TestCase):
    def test_a_fade_starts_and_ends_at_nothing_and_leaves_the_middle_alone(self):
        flat = np.ones(RATE * 2, dtype=np.float32)
        out = mix.fade(flat, fade_in=0.5, fade_out=0.5)
        self.assertEqual(float(out[0]), 0.0)
        self.assertAlmostEqual(float(out[-1]), 0.0, places=5)
        self.assertTrue(np.array_equal(out[RATE // 2:-RATE // 2], flat[RATE // 2:-RATE // 2]))
        self.assertTrue(flat.all(), "the clip handed in is not changed")

    def test_a_fade_holds_its_level_longer_than_a_straight_line(self):
        out = mix.fade(np.ones(RATE, dtype=np.float32), fade_in=1.0)
        self.assertAlmostEqual(float(out[RATE // 2]), math.sqrt(0.5), places=3)

    def test_a_fade_longer_than_the_clip_takes_the_whole_clip(self):
        out = mix.fade(np.ones(100, dtype=np.float32), fade_in=1.0, fade_out=1.0)
        self.assertEqual(len(out), 100)
        self.assertLess(float(out.max()), 1.0)


@needs_numpy
class Panning(unittest.TestCase):
    def test_one_channel_keeps_its_power_wherever_it_is_put(self):
        sound = noise()
        for where in (-1.0, -0.4, 0.0, 0.7, 1.0):
            with self.subTest(where=where):
                placed = mix.pan(sound, where)
                self.assertAlmostEqual(float(np.square(placed).sum()), float(np.square(sound).sum()), places=1)

    def test_hard_left_and_hard_right(self):
        self.assertAlmostEqual(float(np.abs(mix.pan(noise(), -1.0)[:, 1]).max()), 0.0, places=6)
        self.assertAlmostEqual(float(np.abs(mix.pan(noise(), 1.0)[:, 0]).max()), 0.0, places=6)

    def test_two_channels_are_balanced_with_the_middle_left_alone(self):
        both = np.stack([noise(), noise(seed=2)], axis=1)
        self.assertIs(mix.pan(both, 0.0), both)
        right = mix.pan(both, 0.5)
        self.assertTrue(np.array_equal(right[:, 1], both[:, 1]))
        self.assertLess(float(np.abs(right[:, 0]).max()), float(np.abs(both[:, 0]).max()))


@needs_numpy
class Ducking(unittest.TestCase):
    def talk(self):
        """Six seconds: speech from 1 to 2, from 3 to 4, and again a fifth of a second later."""
        sound = np.zeros(RATE * 6, dtype=np.float32)
        sound[RATE:2 * RATE] = noise()
        sound[3 * RATE:4 * RATE] = noise(seed=2)
        sound[int(4.2 * RATE):int(4.5 * RATE)] = noise(0.3, seed=3)
        return sound

    def test_the_gain_dips_while_there_is_sound_and_comes_back_after(self):
        gain = mix.duck(self.talk(), RATE, -8.0)
        self.assertEqual(len(gain), RATE * 6)
        self.assertAlmostEqual(float(gain[int(0.5 * RATE)]), 1.0, places=3)
        self.assertAlmostEqual(20 * math.log10(float(gain[int(1.9 * RATE)])), -8.0, delta=0.1)
        self.assertGreater(float(gain[int(2.95 * RATE)]), 0.7, "it is on its way back up between lines")
        self.assertGreater(float(gain[-1]), 0.7)

    def test_the_dip_is_held_across_a_short_gap(self):
        gain = mix.duck(self.talk(), RATE, -8.0)
        self.assertAlmostEqual(20 * math.log10(float(gain[int(4.1 * RATE)])), -8.0, delta=0.5)

    def test_it_goes_down_as_a_swell_and_not_as_a_step(self):
        gain = mix.duck(self.talk(), RATE, -8.0)
        self.assertLess(float(np.abs(np.diff(gain)).max()), 0.001)

    def test_no_depth_or_no_sound_is_no_dip(self):
        self.assertTrue((mix.duck(self.talk(), RATE, 0.0) == 1.0).all())
        self.assertEqual(len(mix.duck(np.zeros(0, dtype=np.float32), RATE, -8.0)), 0)

    def test_two_channels_are_listened_to_as_one(self):
        both = np.stack([self.talk(), self.talk()], axis=1)
        self.assertTrue(np.allclose(mix.duck(both, RATE, -8.0), mix.duck(self.talk(), RATE, -8.0), atol=1e-6))


@needs_numpy
class Tracks(unittest.TestCase):
    def test_the_built_in_tracks(self):
        known = mix.tracks()
        self.assertEqual(sorted(known), ["ambience", "dialogue", "foreground", "music", "transition", "video"])
        self.assertIsNone(known["dialogue"].level)
        self.assertIsNone(known["video"].level)
        music = known["music"]
        self.assertTrue(music.bed and music.loop and music.exclusive)
        self.assertEqual((music.level, music.duck, music.duck_under), (-30.0, -8.0, ("speech",)))
        self.assertEqual((music.fade_out, music.xfade, music.tail), (3.0, 2.0, 2.0))
        self.assertFalse(known["foreground"].bed)
        self.assertLess(known["ambience"].level, known["foreground"].level)

    def test_a_script_changes_a_built_in_track_and_names_its_own(self):
        known = mix.tracks({"music": {"level": -26, "duck": -12, "duck_under": ["remote"]},
                            "remote": {"compress": True, "reverb": "room", "reverb_mix": 0.1, "pan": 0.3}})
        self.assertEqual((known["music"].level, known["music"].duck, known["music"].duck_under),
                         (-26, -12, ("remote",)))
        self.assertTrue(known["music"].loop, "what the script did not say stays as it was")
        remote = known["remote"]
        self.assertEqual((remote.kind, remote.level, remote.fade_out, remote.pan), ("oneshot", -24.0, 1.0, 0.3))
        self.assertEqual(remote.effects, Effects(reverb="room", reverb_mix=0.1, compress=True))

    def test_the_tracks_of_a_parsed_script(self):
        scene = conv.load(FIXTURES / "tracks.conv")
        known = mix.tracks(scene["tracks"])
        self.assertEqual(known["study"].effects, Effects(reverb="hall", reverb_mix=0.16))
        self.assertEqual(known["remote"].duck, -6)
        self.assertEqual(known["remote"].effects, Effects(reverb="room", compress=True, gain=-2.0))

    def test_how_long_effects_ring(self):
        self.assertEqual(mix.ring(Effects()), 0.0)
        self.assertEqual(mix.ring(Effects(compress=True, gain=-3)), 0.0)
        self.assertEqual(mix.ring(Effects(reverb="hall")), 2.5)
        self.assertAlmostEqual(mix.ring(Effects(delay=0.5)), 2.0)
        self.assertAlmostEqual(mix.ring(Effects(delay=0.5, delay_feedback=0.9)), 4.5)
        self.assertEqual(mix.ring(Effects(reverb="room", delay=0.1)), 2.5)


@needs_numpy
class Mixing(InAFolder):
    def scene(self):
        """Speech over music and a room, with a second voice on a track of its own."""
        mixer = mix.Mixer(mix.tracks({"remote": {"pan": 0.3}}))
        mixer.speech.add("remote")
        talk = np.zeros(RATE * 4, dtype=np.float32)
        talk[RATE:2 * RATE] = noise()
        mixer.place("dialogue", talk, 0.0)
        mixer.place("remote", noise(seed=5), 2.5, pan=-0.5)
        mixer.place("music", np.stack([tone(5.0, 220), tone(5.0, 330)], axis=1), 0.0)
        mixer.place("ambience", noise(5.0, loud=0.02, seed=7), 0.0)
        return mixer

    def test_the_stems_add_up_to_the_mix(self):
        done = self.scene().mix()
        self.assertEqual(sorted(done.stems), ["ambience", "dialogue", "music", "remote"])
        self.assertEqual(done.samples.shape, (5 * RATE + 1, 2))
        self.assertLess(float(np.abs(sum(done.stems.values()) - done.samples).max()), 1e-6)
        self.assertEqual(done.report["clips"], 4)
        self.assertEqual(done.report["seconds"], 5.0)
        self.assertFalse(done.report["limited"])

    def test_music_dips_under_every_voice_and_the_room_does_not(self):
        done = self.scene().mix()
        music, room = done.stems["music"], done.stems["ambience"]
        loud = lambda sound, start, end: mix.loudness(sound[int(start * RATE):int(end * RATE)])
        self.assertAlmostEqual(loud(music, 1.5, 1.9) - loud(music, 0.2, 0.8), -8.0, delta=0.5)
        self.assertAlmostEqual(loud(music, 3.0, 3.4) - loud(music, 0.2, 0.8), -8.0, delta=0.5)
        self.assertAlmostEqual(loud(room, 1.5, 1.9), loud(room, 0.2, 0.8), delta=0.5)
        self.assertEqual(done.report["tracks"]["music"]["ducked_under"], ["dialogue", "remote"])
        self.assertEqual(done.report["tracks"]["music"]["duck_db"], -8.0)
        self.assertNotIn("ducked_under", done.report["tracks"]["ambience"])

    def test_a_voice_on_a_track_that_is_not_speech_does_not_duck_the_music(self):
        mixer = self.scene()
        mixer.speech.discard("remote")
        music = mixer.mix().stems["music"]
        loud = lambda start, end: mix.loudness(music[int(start * RATE):int(end * RATE)])
        self.assertAlmostEqual(loud(4.3, 4.9), loud(0.2, 0.8), delta=0.5)
        self.assertAlmostEqual(loud(3.2, 3.4), loud(0.2, 0.8), delta=2.0)

    def test_a_mix_past_the_ceiling_is_turned_down_whole_with_its_stems(self):
        mixer = mix.Mixer()
        mixer.place("dialogue", tone(1.0, loud=0.9), 0.0, pan=-1.0)
        mixer.place("foreground", tone(1.0, loud=0.9), 0.0, pan=-1.0)
        done = mixer.mix()
        self.assertTrue(done.report["limited"])
        self.assertAlmostEqual(done.report["peak_before_db"], 20 * math.log10(1.8), places=1)
        self.assertAlmostEqual(float(np.abs(done.samples).max()), mix.CEILING, places=5)
        self.assertLess(float(np.abs(sum(done.stems.values()) - done.samples).max()), 1e-6)
        self.assertAlmostEqual(float(np.abs(done.stems["dialogue"]).max()), mix.CEILING / 2, places=4)

    def test_a_fader_moves_over_a_ramp_and_stays(self):
        mixer = mix.Mixer()
        mixer.place("foreground", np.ones(RATE * 4, dtype=np.float32) * 0.1, 0.0, pan=-1.0)
        mixer.move("foreground", 1.0, 0.5, ramp=2.0)
        left = mixer.mix().stems["foreground"][:, 0]
        self.assertAlmostEqual(float(left[RATE // 2]), 0.1, places=5)
        self.assertAlmostEqual(float(left[2 * RATE]), 0.075, places=4)
        self.assertAlmostEqual(float(left[int(3.5 * RATE)]), 0.05, places=5)

    def test_a_track_has_a_volume_and_a_place_of_its_own(self):
        mixer = mix.Mixer(mix.tracks({"side": {"volume": 0.5, "pan": 1.0}}))
        mixer.place("side", np.stack([tone(), tone()], axis=1), 0.0)
        stem = mixer.mix().stems["side"]
        self.assertAlmostEqual(float(np.abs(stem[:, 0]).max()), 0.0, places=6)
        self.assertAlmostEqual(float(np.abs(stem[:, 1]).max()), 0.25, places=4)

    def test_a_track_can_be_changed_for_the_whole_mix(self):
        mixer = mix.Mixer()
        self.assertEqual(mixer.change("music", volume=0.5, fade_out=1.0).volume, 0.5)
        self.assertEqual(mixer.track("music").fade_out, 1.0)
        self.assertTrue(mixer.track("music").loop)
        self.assertEqual(mixer.track("never-declared").level, -24.0)

    def test_clips_start_where_they_are_put_and_nothing_starts_before_the_start(self):
        mixer = mix.Mixer()
        mixer.place("foreground", np.ones(100, dtype=np.float32), 0.5, pan=-1.0)
        mixer.place("foreground", np.ones(100, dtype=np.float32), -3.0, pan=-1.0)
        mixer.place("foreground", np.zeros(0, dtype=np.float32), 9.0)
        left = mixer.mix().stems["foreground"][:, 0]
        self.assertEqual(len(left), RATE // 2 + 101)
        self.assertEqual(np.flatnonzero(left).tolist(), [*range(100), *range(RATE // 2, RATE // 2 + 100)])
        self.assertAlmostEqual(mixer.seconds, 0.5, places=2)

    def test_an_empty_mix_is_a_moment_of_quiet(self):
        done = mix.Mixer().mix()
        self.assertEqual(done.samples.shape, (1, 2))
        self.assertEqual(done.stems, {})

    @needs_ffmpeg
    def test_a_track_s_effects_go_through_the_effects_backend(self):
        mixer = mix.Mixer(mix.tracks({"quiet": {"gain": -6}}))
        mixer.place("quiet", tone(loud=0.4), 0.0, pan=-1.0)
        mixer.place("foreground", tone(loud=0.4), 0.0, pan=-1.0)
        done = mixer.mix()
        self.assertAlmostEqual(float(np.abs(done.stems["quiet"]).max()), 0.4 * 10 ** (-6 / 20), places=3)
        self.assertAlmostEqual(float(np.abs(done.stems["foreground"]).max()), 0.4, places=4)
        self.assertEqual(done.report["tracks"]["quiet"]["effects"], {"gain": -6.0})
        self.assertIsNone(done.report["tracks"]["foreground"]["effects"])

    @needs_ffmpeg
    def test_a_track_louder_than_full_scale_goes_through_its_effects_unclipped(self):
        mixer = mix.Mixer(mix.tracks({"hot": {"gain": -12}}))
        mixer.place("hot", tone(loud=0.9), 0.0, pan=-1.0, volume=3.0)
        done = mixer.mix()
        # 2.7 at its loudest going in. Clipped on the way it would come out at a quarter.
        self.assertAlmostEqual(float(np.abs(done.stems["hot"]).max()), 2.7 * 10 ** (-12 / 20), places=2)

    @needs_ffmpeg
    def test_a_reverb_rings_on_after_the_last_clip(self):
        mixer = mix.Mixer(mix.tracks({"hall": {"reverb": "hall"}}))
        mixer.place("hall", tone(0.5), 0.0)
        done = mixer.mix()
        self.assertAlmostEqual(done.report["seconds"], 3.0, places=2)
        after = done.stems["hall"][int(0.55 * RATE):int(0.8 * RATE)]
        self.assertGreater(float(np.abs(after).max()), 0.01)

    @needs_ffmpeg
    def test_a_mix_is_written_with_a_file_for_each_track(self):
        mixer = self.scene()
        report = mixer.write(self.here / "out" / "scene.wav", stems=self.here / "out" / "stems")
        self.assertEqual(report["path"], str(self.here / "out" / "scene.wav"))
        self.assertEqual(sorted(Path(file).name for file in report["stems"].values()),
                         ["ambience.wav", "dialogue.wav", "music.wav", "remote.wav"])
        self.assertEqual(ffmpeg.probe(report["path"]).codec, "pcm_s24le")
        whole = pcm.read(report["path"], rate=RATE)
        parts = sum(pcm.read(file, rate=RATE) for file in report["stems"].values())
        self.assertLess(float(np.abs(whole - parts).max()), 1e-5)

    def test_a_mix_is_written_alone_when_no_stems_are_asked_for(self):
        report = self.scene().write(self.here / "scene.wav", bits=16)
        self.assertNotIn("stems", report)
        self.assertEqual([file.name for file in self.here.iterdir()], ["scene.wav"])

    def test_a_track_whose_name_is_not_a_file_name_is_not_written(self):
        mixer = mix.Mixer()
        mixer.place("../elsewhere", tone(0.1), 0.0)
        with self.assertRaises(ValueError):
            mix.write_stems(mixer.mix(), self.here / "stems")
        self.assertFalse((self.here / "elsewhere.wav").exists())


if __name__ == "__main__":
    unittest.main()
