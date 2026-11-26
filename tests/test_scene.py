"""A script rendered as sound.

No model speaks or listens here: stand-ins do. Every sound is made on the
spot, and where each landed is read back from the track it was put on.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from constructive_media import conv
from constructive_media.speech import GivenVoices, SpeechError, Voice

from .support import (HAVE_NUMPY, DatabaseTest, Listener, Speaker, make_still, make_video,
                      needs_ffmpeg, needs_numpy)
from .test_cli import run

if HAVE_NUMPY:
    import numpy as np

    from constructive_media.audio import line, mix, pcm, scene

VOICES = GivenVoices([Voice("ada", profile="ada-plain"), Voice("ada", "warm", profile="ada-warm"),
                      Voice("grace", profile="grace-plain")])
CAST = "@characters\nADA: { persona: ada }\nGRACE: { voice: grace }\n"
SOUNDS = ("@sounds\n"
          "DING: { file: sounds/ding.wav }\n"
          "RAIN: { file: sounds/rain.wav }\n"
          "SONG: { file: sounds/song.wav }\n"
          "TUNE: { file: sounds/tune.wav }\n")
MUDDLE = "ba ko mi ru te la no si du fe"


@needs_numpy
@needs_ffmpeg
class Rendered(unittest.TestCase):
    """A folder with a few sounds in it, a script written into it, and stand-ins to say it."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.here = Path(folder.name).resolve()
        noise = np.random.default_rng(5)
        self.sound("ding", np.sin(np.arange(24_000) * 0.2) * 0.5)                      # half a second
        self.sound("rain", noise.standard_normal(3 * 48_000) * 0.1)                    # three seconds
        self.sound("song", np.sin(np.arange(4 * 48_000) * 0.05) * 0.3)                 # four seconds
        self.sound("tune", np.sin(np.arange(4 * 48_000) * 0.08) * 0.3)
        self.speaker = Speaker()
        self.listener = Listener(self.speaker)
        self.told: list[str] = []

    def sound(self, name, samples, folder="sounds"):
        return pcm.write(self.here / folder / f"{name}.wav", np.asarray(samples, dtype=np.float32), 48_000)

    def script(self, body, head=CAST + SOUNDS, name="night.conv"):
        path = self.here / name
        path.write_text(f"{head}---\n{body}\n", encoding="utf-8")
        return path

    def render(self, body=None, target="out/night.wav", **how):
        if body is not None:
            self.script(body, **{key: how.pop(key) for key in ("head",) if key in how})
        how.setdefault("voices", VOICES)
        how.setdefault("transcriber", self.listener)
        return scene.render(self.here / "night.conv", self.here / target, synthesizer=lambda engine: self.speaker,
                            tell=self.told.append, **how)

    def stem(self, name):
        return pcm.read(self.here / "out/night.stems" / f"{name}.wav")

    def loud(self, name, start, end):
        """How loud a track is between two times, in dB."""
        part = self.stem(name)[int(start * 48_000):int(end * 48_000)]
        return 20 * np.log10(max(float(np.sqrt((part ** 2).mean())) if len(part) else 0.0, 1e-9))

    def placed(self, report, sound):
        return [one for one in report["sound"]["sounds"] if one["sound"] == sound]


class Walking(Rendered):
    def test_lines_follow_one_another_with_a_gap_between(self):
        report = self.render("ADA: The lantern is lit.\nGRACE: I am on my way over.\n[pause 1.5]\nADA: Mind the step.",
                             stems=True, loudness=None)
        first, second, third = report["lines"]
        self.assertEqual([one["line"] for one in report["lines"]], [1, 2, 3])
        self.assertEqual([one["persona"] for one in report["lines"]], ["ada", "grace", "ada"])
        self.assertEqual([one["script_line"] for one in report["lines"]], [10, 11, 13])
        self.assertEqual(first["at"], 0.0)
        self.assertAlmostEqual(second["at"], first["seconds"] + scene.GAP, places=2)
        self.assertAlmostEqual(third["at"], second["at"] + second["seconds"] + scene.GAP + 1.5, places=2)
        self.assertEqual([one["status"] for one in report["lines"]], [line.CLEAN] * 3)
        self.assertEqual(first["text"], "The lantern is lit.")
        # Each line keeps a seed of its own, whatever happens to the others.
        self.assertEqual([one["seed"] for one in report["lines"]], [507, 514, 521])
        for one in report["lines"]:
            self.assertAlmostEqual(pcm.seconds(one["file"]), one["seconds"], places=2)
        self.assertEqual(Path(first["file"]), self.here / "out/night.lines/line_001.wav")
        # Speech where the lines are, quiet in the pause.
        self.assertGreater(self.loud("dialogue", 0.3, first["seconds"] - 0.3), -40)
        self.assertLess(self.loud("dialogue", third["at"] - 1.4, third["at"] - 0.1), -80)
        # The scene lasts to the end of the clock, the last gap and all.
        self.assertAlmostEqual(report["seconds"], third["at"] + third["seconds"] + scene.GAP, places=2)
        self.assertAlmostEqual(pcm.seconds(report["output"]), report["seconds"], places=2)
        self.assertEqual(report["speech"], {"adapters": ["stand-in"], "models": ["made-up-voice"]})
        self.assertEqual(report["listening"], {"adapter": "stand-in", "model": "made-up-ears"})
        self.assertEqual(report["needs_review"], 0)

    def test_the_report_is_written_beside_the_output(self):
        report = self.render("ADA: The lantern is lit.")
        manifest = self.here / "out/night.manifest.json"
        self.assertEqual(report["manifest"], str(manifest))
        self.assertEqual(json.loads(manifest.read_text()), json.loads(json.dumps(report)))
        self.assertEqual(report["label"], "night")
        self.assertEqual(self.render(label="first night")["label"], "first night")

    def test_a_pause_at_the_end_is_kept(self):
        report = self.render("ADA: The lantern is lit.\n[pause 2]", loudness=None)
        self.assertAlmostEqual(report["seconds"], report["lines"][0]["seconds"] + scene.GAP + 2, places=2)

    def test_the_scene_waits_only_for_a_sound_that_holds(self):
        report = self.render("[DING]\nADA: The lantern is lit.\n[DING hold]\nADA: Mind the step.", stems=True)
        first, second = report["lines"]
        early, late = self.placed(report, "DING")
        self.assertEqual((early["at"], early["track"], early["seconds"]), (0.0, "foreground", 0.5))
        self.assertEqual(first["at"], 0.0)                     # nothing waited for the first
        self.assertAlmostEqual(late["at"], first["seconds"] + scene.GAP, places=2)
        self.assertAlmostEqual(second["at"], late["at"] + 0.5, places=2)       # the second was waited for
        self.assertGreater(self.loud("foreground", late["at"], late["at"] + 0.4), -40)
        self.assertLess(self.loud("foreground", 1.0, late["at"] - 0.1), -80)

    def test_a_sound_can_be_put_at_a_time_of_its_own(self):
        report = self.render("[pause 2]\n[DING at:+1.5]\n[DING at:0.25]\n[DING at:-9]")
        self.assertEqual([one["at"] for one in self.placed(report, "DING")], [3.5, 0.25, 0.0])

    def test_a_sound_lasts_as_long_as_it_is_asked_to(self):
        report = self.render("[RAIN for:1]\n[pause 5]", stems=True)
        rain, = self.placed(report, "RAIN")
        self.assertEqual((rain["seconds"], rain["fade_out"]), (1.0, 0.3))
        self.assertLess(self.loud("foreground", 1.1, 4.0), -80)
        # A held sound that is asked to last a while is waited for that long.
        report = self.render("[RAIN for:1 hold]\nADA: The lantern is lit.")
        self.assertEqual(report["lines"][0]["at"], 1.0)

    def test_a_bed_plays_to_the_end_unless_it_is_stopped(self):
        report = self.render("[ambience RAIN]\n[music SONG]\n[pause 10]\n[ambience off]\n[pause 4]", stems=True)
        rain, = self.placed(report, "RAIN")
        song, = self.placed(report, "SONG")
        # The rain is three seconds long, loops, and goes out over the track's own fade when stopped.
        self.assertEqual((rain["at"], rain["seconds"], rain["fade_in"], rain["fade_out"]), (0.0, 11.2, 0.8, 1.2))
        self.assertGreater(self.loud("ambience", 8.0, 9.5), -50)
        self.assertLess(self.loud("ambience", 11.3, 14.0), -80)
        # Nothing stops the music, so it plays out the scene and its tail past it.
        self.assertEqual((song["seconds"], song["fade_out"]), (16.0, 3.0))
        self.assertAlmostEqual(report["seconds"], 16.0, places=2)

    def test_a_stop_can_name_the_sound_it_stops(self):
        report = self.render("[ambience RAIN]\n[ambience SONG]\n[pause 4]\n[ambience off RAIN fade-out:0.5]\n[pause 4]")
        rain, = self.placed(report, "RAIN")
        song, = self.placed(report, "SONG")
        self.assertEqual((rain["seconds"], rain["fade_out"]), (4.5, 0.5))
        self.assertEqual(song["seconds"], 8.0)

    def test_on_a_track_that_plays_one_thing_the_next_takes_over(self):
        report = self.render("[music SONG]\n[pause 5]\n[music TUNE]\n[pause 5]", stems=True)
        song, = self.placed(report, "SONG")
        tune, = self.placed(report, "TUNE")
        # The first goes out over the track's fade from where the second comes in, and the second swells in as long.
        self.assertEqual((song["seconds"], song["fade_out"]), (8.0, 3.0))
        self.assertEqual((tune["at"], tune["fade_in"]), (5.0, 3.0))
        self.assertEqual(tune["seconds"], 7.0)          # to the end of the scene and the music's tail past it

    def test_a_sound_started_partway_through_is_eased_in(self):
        report = self.render("[music SONG start:1 noloop]\n[pause 2]")
        song, = self.placed(report, "SONG")
        self.assertEqual(song["fade_in"], scene.EASED_IN)
        report = self.render("[music SONG start:1 fade-in:0.2]\n[pause 2]")
        self.assertEqual(self.placed(report, "SONG")[0]["fade_in"], 0.2)

    def test_a_fader_is_moved_while_the_scene_plays(self):
        self.render("[music SONG]\n[pause 4]\n[music vol:0.25 ramp:1]\n[pause 5]", stems=True, loudness=None)
        self.assertAlmostEqual(self.loud("music", 6.0, 8.0) - self.loud("music", 1.0, 3.0), -12.04, places=0)

    def test_a_sound_written_inside_a_line_lands_partway_through_it(self):
        report = self.render("ADA: One two three four. <sfx DING> Five six seven eight.", stems=True)
        said, = report["lines"]
        ding, = self.placed(report, "DING")
        self.assertEqual(said["text"], "One two three four. Five six seven eight.")
        self.assertAlmostEqual(ding["at"], said["seconds"] * 19 / 40, places=2)
        self.assertEqual(ding["track"], "foreground")

    def test_a_line_with_nothing_to_say_still_plays_its_sounds(self):
        report = self.render("ADA: <sfx DING> <break 1.5>\nADA: The lantern is lit.")
        self.assertEqual(len(report["lines"]), 1)
        self.assertEqual(report["lines"][0]["at"], 1.5)
        self.assertEqual(self.placed(report, "DING")[0]["at"], 0.0)

    def test_a_voice_on_a_track_of_its_own_still_pulls_the_music_down(self):
        head = CAST.replace("{ voice: grace }", "{ voice: grace, track: remote }") + SOUNDS + "@tracks\nremote: { pan: -0.4 }\n"
        report = self.render("[music SONG]\nGRACE: I am on my way over to you now.\n[pause 3]", head=head, stems=True,
                             loudness=None)
        said, = report["lines"]
        self.assertEqual(said["track"], "remote")
        self.assertEqual(sorted(report["stems"]), ["music", "remote"])
        self.assertEqual(report["sound"]["tracks"]["music"]["ducked_under"], ["remote"])
        self.assertLess(self.loud("music", 0.8, said["seconds"] - 0.3), self.loud("music", said["seconds"] + 1.5, said["seconds"] + 3) - 5)

    def test_marks_land_where_the_clock_stands(self):
        report = self.render("[slide one]\nADA: The lantern is lit.\n[slide two step:1]\nADA: Mind the step.",
                             head=CAST.replace("{ persona: ada }", '{ persona: ada, name: "Ada Field", title: "Keeper" }') + SOUNDS)
        first, second = report["lines"]
        self.assertEqual([(mark["kind"], mark["t"]) for mark in report["marks"]],
                         [("slide", 0.0), ("line", 0.0), ("slide", second["at"]), ("line", second["at"])])
        self.assertEqual(report["marks"][2]["id"], "two")
        spoken = report["marks"][1]
        self.assertEqual((spoken["speaker"], spoken["persona"], spoken["name"], spoken["title"], spoken["seconds"]),
                         ("ADA", "ada", "Ada Field", "Keeper", first["seconds"]))

    def test_a_clip_takes_the_screen_and_the_scene_waits_for_it(self):
        make_video(self.here / "clips/boat.mp4", colours=("red",), seconds=3.0)
        head = CAST + SOUNDS + "CLIP: { file: clips/boat.mp4 }\n"
        report = self.render("ADA: The lantern is lit.\n[video CLIP start:1 for:1.5]\nADA: Mind the step.", head=head)
        first, second = report["lines"]
        mark = next(mark for mark in report["marks"] if mark["kind"] == "video")
        self.assertEqual((mark["sound"], mark["start"], mark["seconds"]), ("CLIP", 1.0, 1.5))
        self.assertEqual(mark["file"], str(self.here / "clips/boat.mp4"))
        self.assertAlmostEqual(mark["t"], first["seconds"] + scene.GAP, places=2)
        self.assertAlmostEqual(second["at"], mark["t"] + 1.5, places=2)
        # With no length asked for, it is waited for to its end.
        report = self.render("[video CLIP start:1]\nADA: Mind the step.", head=head)
        self.assertAlmostEqual(report["lines"][0]["at"], 2.0, places=1)

    def test_the_old_way_of_playing_a_file_as_music_still_plays_it(self):
        report = self.render("[music: sounds/song.wav vol:0.5]\n[pause 3]")
        song, = self.placed(report, "sounds/song.wav")
        self.assertEqual((song["track"], song["seconds"]), ("music", 5.0))


class Speaking(Rendered):
    def test_a_line_is_said_in_the_voice_for_its_emotion(self):
        report = self.render("ADA[warm]: The lantern is lit.\nADA[whisper]: Mind the step.\nGRACE: I am on my way.")
        self.assertEqual([voice.profile for _, voice, _ in self.speaker.asked], ["ada-warm", "ada-plain", "grace-plain"])
        self.assertEqual([one["emotion"] for one in report["lines"]], ["warm", "neutral", "neutral"])
        self.assertEqual(self.told[0].strip(), "ada has no whisper voice; such lines are said in another of its voices")
        self.assertIn("[001] ada/warm seed 507, 1 take, clean", self.told[1])

    def test_a_characters_effects_are_put_on_its_lines(self):
        head = CAST.replace("{ persona: ada }", "{ persona: ada, gain: -12 }") + SOUNDS
        plain = self.render("ADA: The lantern is lit.", loudness=None, stems=True)
        loud = self.loud("dialogue", 0.2, 1.5)
        quiet = self.render("ADA: The lantern is lit.", head=head, loudness=None, stems=True)
        self.assertIsNone(plain["lines"][0]["effects"])
        self.assertEqual(quiet["lines"][0]["effects"], {"gain": -12})
        self.assertAlmostEqual(self.loud("dialogue", 0.2, 1.5) - loud, -12.0, places=0)

    def test_a_line_no_take_of_which_passes_is_kept_and_marked(self):
        self.listener.hears = lambda text, seed: MUDDLE if "step" in text else text
        head = CAST.replace("{ persona: ada }", "{ persona: ada, gain: -12 }") + SOUNDS
        report = self.render("GRACE: I am on my way over.\nADA: Mind the step by the old door.", head=head, attempts=2,
                             more_seeds=0)
        good, bad = report["lines"]
        self.assertEqual((good["status"], bad["status"]), (line.CLEAN, line.NEEDS_REVIEW))
        self.assertEqual(report["needs_review"], 1)
        self.assertEqual((bad["heard"], bad["reason"], bad["word_error"]), (MUDDLE, "garbled", 1.0))
        self.assertGreater(bad["seconds"], 1.0)             # it is in the scene all the same
        self.assertIsNone(bad["effects"])                   # and left as it was said, for whoever listens
        self.assertIn("needs a listen (garbled", self.told[-1])

    def test_when_the_first_line_cannot_be_said_at_all_the_render_stops(self):
        self.speaker.wrong = lambda text, seed: ConnectionError("nobody is answering")
        with self.assertRaises(SpeechError) as raised:
            self.render("ADA: The lantern is lit.\nGRACE: I am on my way over.", attempts=2, more_seeds=1)
        self.assertEqual(str(raised.exception), "the first line could not be said: nobody is answering")
        self.assertEqual({text for text, _, _ in self.speaker.asked}, {"The lantern is lit."})
        self.assertFalse((self.here / "out/night.wav").exists())

    def test_a_later_line_that_cannot_be_said_is_quiet_and_marked(self):
        self.speaker.wrong = lambda text, seed: ConnectionError("gone") if "step" in text else None
        report = self.render("GRACE: I am on my way over.\nADA: Mind the step.\nGRACE: I see it.", attempts=2, more_seeds=1)
        first, second, third = report["lines"]
        self.assertEqual((second["status"], second["reason"], second["seconds"]), (line.NEEDS_REVIEW, line.ERROR, scene.QUIET))
        self.assertAlmostEqual(third["at"], second["at"] + scene.QUIET + scene.GAP, places=2)
        self.assertEqual(third["status"], line.CLEAN)

    def test_only_some_lines_are_said_again(self):
        body = "ADA: The lantern is lit.\nGRACE: I am on my way over.\nADA: Mind the step."
        first = self.render(body)
        self.told.clear()
        self.speaker.asked.clear()
        again = self.render(only="grace")
        self.assertEqual([text for text, _, _ in self.speaker.asked], ["I am on my way over."])
        self.assertEqual([one.get("reused", False) for one in again["lines"]], [True, False, True])
        self.assertEqual([one["at"] for one in again["lines"]], [one["at"] for one in first["lines"]])
        self.assertEqual(again["lines"][0]["seed"], first["lines"][0]["seed"])
        self.assertEqual(again["speech"], first["speech"])
        self.assertIn("[001] ada: kept as it was", self.told[0])
        # By number, and several at once.
        self.speaker.asked.clear()
        self.render(only=[1, "3"])
        self.assertEqual([text for text, _, _ in self.speaker.asked], ["The lantern is lit.", "Mind the step."])
        # A line whose words have changed is said again whoever was asked for.
        self.speaker.asked.clear()
        self.render(body.replace("Mind the step.", "Mind the gap."), only="grace")
        self.assertEqual([text for text, _, _ in self.speaker.asked], ["I am on my way over.", "Mind the gap."])

    def test_only_must_name_somebody_or_a_line(self):
        self.script("ADA: The lantern is lit.")
        for wrong in ("grace", "2", "0"):
            with self.subTest(only=wrong), self.assertRaises(SpeechError) as raised:
                self.render(only=wrong)
            self.assertIn("neither somebody who speaks in this script (ada) nor one of its 1 lines", str(raised.exception))
        self.assertEqual(self.speaker.asked, [])
        self.render(only="ada")
        self.assertIn("nothing was rendered here before", self.told[0])

    def test_a_script_with_nothing_said_needs_nothing_that_listens(self):
        report = self.render("[DING]\n[pause 1]", transcriber=None)
        self.assertEqual((report["lines"], report["listening"]), ([], None))
        self.assertEqual(report["speech"], {"adapters": [], "models": []})


class Finishing(Rendered):
    def test_the_stems_add_up_to_the_mix(self):
        report = self.render("[music SONG]\nADA: The lantern is lit.\n[DING hold]\n[pause 1]", stems=True, loudness=None)
        self.assertIsNone(report["loudness"])
        self.assertEqual(sorted(report["stems"]), ["dialogue", "foreground", "music"])
        whole = pcm.read(report["output"])
        together = sum(pcm.read(path) for path in report["stems"].values())
        self.assertEqual(whole.shape, together.shape)
        self.assertLess(float(np.abs(whole - together).max()), 1e-4)
        self.assertIsNone(self.render(loudness=None)["stems"])

    def test_the_whole_is_brought_to_one_loudness(self):
        body = "[music SONG]\nADA: The lantern under the harbour wall is lit at dusk.\nGRACE: I am on my way over to see it."
        report = self.render(body)
        self.assertEqual((report["loudness"]["asked"], report["loudness"]["true_peak"]), (-16.0, -1.5))
        # Turned up whole: nothing about its sound has changed.
        self.assertEqual(report["loudness"]["how"], "gain")
        self.assertAlmostEqual(report["loudness"]["is"], -16.0, delta=0.2)
        self.assertLessEqual(report["loudness"]["peak"], -1.5)
        loud = mix.loudness(pcm.read(report["output"]))
        self.assertEqual(pcm.rate(report["output"]), 48_000)
        quiet = self.render(loudness=-27.0)
        self.assertAlmostEqual(quiet["loudness"]["is"], -27.0, delta=0.2)
        self.assertEqual(quiet["loudness"]["was"], report["loudness"]["was"])
        self.assertAlmostEqual(loud - mix.loudness(pcm.read(quiet["output"])), 11.0, delta=0.1)

    def test_a_mix_whose_peaks_would_pass_the_ceiling_is_squeezed_to_fit(self):
        hiss = np.random.default_rng(9).standard_normal(12 * 48_000) * 0.004
        hiss[::24_000] = 0.9                                 # a click every half second, far above the rest
        mixed = self.sound("clicks", hiss, folder="mixes")
        told = scene.finish(mixed, self.here / "out/clicks.wav")
        self.assertEqual(told["how"], "dynamic")
        self.assertGreater(told["is"], told["was"] + 6)
        self.assertLess(mix.peak(pcm.read(self.here / "out/clicks.wav")), -1.0)
        self.assertEqual(pcm.rate(self.here / "out/clicks.wav"), 48_000)

    def test_a_scene_with_nothing_in_it_is_written_as_it_came(self):
        report = self.render("[pause 2]")
        self.assertIsNone(report["loudness"])
        self.assertAlmostEqual(pcm.seconds(report["output"]), 2.0, places=2)

    def test_the_kind_of_file_is_the_one_the_name_asks_for(self):
        report = self.render("ADA: The lantern is lit.", target="out/night.flac")
        self.assertEqual(Path(report["output"]).read_bytes()[:4], b"fLaC")
        self.assertTrue((self.here / "out/night.lines/line_001.wav").is_file())

    def test_a_kind_of_file_that_cannot_be_written_is_found_out_first(self):
        self.script("ADA: The lantern is lit.")
        for target, said in (("out/night", "does not say what kind of file"),
                             ("out/night.nosuchkind", "cannot write .nosuchkind files")):
            with self.subTest(target=target), self.assertRaises(SpeechError) as raised:
                self.render(target=target)
            self.assertIn(said, str(raised.exception))
        self.assertEqual(self.speaker.asked, [])


class Checking(Rendered):
    def problems(self, body, head=CAST + SOUNDS, **how):
        self.script(body, head=head)
        with self.assertRaises(conv.ConvError) as raised:
            self.render(**how)
        self.assertEqual(self.speaker.asked, [])        # nothing is said for a script that cannot be rendered
        return raised.exception.problems

    def test_everything_missing_is_reported_at_once_by_line(self):
        head = CAST + "BORIS: { persona: boris }\n" + SOUNDS + 'BELL: { file: sounds/bell.wav }\nWIND: { prompt: "wind over water" }\n'
        found = self.problems("[BELL]\nADA: The lantern is lit. <sfx WIND>\n[BELL]\nBORIS: Is it.\nBORIS: It is.", head=head)
        self.assertEqual([one["line"] for one in found], [13, 14, 16])
        self.assertEqual(found[0]["text"], "[BELL]")
        self.assertIn("`BELL` plays `sounds/bell.wav`, and there is no such file.", found[0]["message"])
        self.assertIn(f"Looked in: {self.here}", found[0]["message"])
        self.assertIn("`WIND` is made from a prompt", found[1]["message"])
        self.assertEqual(found[2]["message"],
                         "`BORIS` speaks as `boris`, and `boris` has no voice.\nVoices: ada, grace")

    def test_a_voice_that_is_nearly_named_is_pointed_at(self):
        found = self.problems("ADA: The lantern is lit.", head=CAST.replace("persona: ada", "persona: adda") + SOUNDS)
        self.assertIn("`adda` has no voice. Did you mean `ada`?", found[0]["message"])
        found = self.problems("HARBOURMASTER: Who goes there.")
        self.assertIn("`HARBOURMASTER` is not under @characters, so its persona is its name in small letters.",
                      found[0]["message"])

    def test_a_file_must_hold_what_its_track_plays(self):
        make_still(self.here / "sounds/card.png")
        (self.here / "sounds/notes.wav").write_text("not a sound at all")
        make_video(self.here / "clips/silent.mp4", seconds=1.0)
        head = (CAST + SOUNDS + "CARD: { file: sounds/card.png }\nNOTES: { file: sounds/notes.wav }\n"
                "CLIP: { file: clips/silent.mp4 }\n")
        found = self.problems("[CARD]\n[NOTES]\n[video SONG]\n[video CLIP]", head=head)
        self.assertEqual([one["message"] for one in found], [
            "`CARD` plays `sounds/card.png`, which has no sound in it",
            "`NOTES` plays `sounds/notes.wav`, which holds nothing that can be played",
            "`[video SONG]` needs a file with a picture, and `sounds/song.wav` has only sound",
        ])

    def test_sounds_made_from_a_prompt_can_be_left_out(self):
        head = CAST + SOUNDS + 'WIND: { prompt: "wind over water" }\n'
        self.script("[WIND]\nADA: The lantern is lit. <sfx WIND>\n[DING]", head=head)
        report = self.render(without_generated=True)
        self.assertEqual(report["sound"]["left_out"], ["WIND"])
        self.assertEqual([one["sound"] for one in report["sound"]["sounds"]], ["DING"])

    def test_sound_files_are_looked_for_in_the_folders_given_first(self):
        self.sound("ding", np.zeros(48_000) + 0.2, folder="library/sounds")       # a second long, where the other is half
        self.script("[DING]\n[pause 2]")
        report = self.render(sounds=[self.here / "library"])
        self.assertEqual(self.placed(report, "DING")[0]["seconds"], 1.0)
        self.assertEqual(scene.folders(self.here / "night.conv", [self.here / "library"])[:2],
                         [self.here / "library", self.here])
        self.assertEqual(scene.find(str(self.here / "sounds/ding.wav"), []), self.here / "sounds/ding.wav")
        self.assertIsNone(scene.find(str(self.here / "sounds/none.wav"), [self.here]))

    def test_a_script_that_is_wrong_in_itself_is_reported_as_ever(self):
        self.script("[NOSUCH]")
        with self.assertRaises(conv.ConvError):
            self.render()


@needs_numpy
@needs_ffmpeg
class Recording(DatabaseTest, Rendered):
    def setUp(self):
        DatabaseTest.setUp(self)
        Rendered.setUp(self)
        from constructive_media import speech

        speech.keep(Voice("Ada", engine="stand-in", profile="ada-plain"), new=True)
        speech.keep(Voice("Grace", engine="stand-in", profile="grace-plain"), new=True)

    def rows(self, sql, *params):
        return [tuple(row) for row in self.query(sql, params)]

    def test_a_render_is_written_down_with_its_lines_and_what_to_listen_to(self):
        self.listener.hears = lambda text, seed: MUDDLE if "step" in text else text
        report = self.render("GRACE[warm]: I am on my way over.\nADA[ghostly]: Mind the step by the old door.",
                             voices=None, attempts=2, more_seeds=0, label="first night")
        render_id = scene.record(report)
        self.assertEqual(self.rows("SELECT label, script_path, output_path, engine_tts, engine_stt, gap_seconds::float, "
                                   "params->>'needs_review', params->'lines'->1->>'status' "
                                   "FROM media_audio.renders WHERE id = %s", render_id),
                         [("first night", str(self.here / "night.conv"), str(self.here / "out/night.wav"), "stand-in",
                           "stand-in", 0.45, "1", "needs_review")])
        chunks = self.rows("SELECT c.line_no, p.slug, c.emotion, c.text, c.audio_path, c.seconds::float "
                           "FROM media_audio.render_chunks c JOIN media.personas p ON p.id = c.persona_id "
                           "WHERE c.render_id = %s ORDER BY c.line_no", render_id)
        self.assertEqual([row[:4] for row in chunks], [(1, "grace", "neutral", "I am on my way over."),
                                                       (2, "ada", "neutral", "Mind the step by the old door.")])
        self.assertEqual(chunks[0][4:], (report["lines"][0]["file"], report["lines"][0]["seconds"]))
        self.assertEqual(self.rows("SELECT render_label, line_no, intended_text, spoken_text, heard_text, "
                                   "word_error::float, attempts, audio_path, verdict "
                                   "FROM media_audio.review_queue WHERE render_id = %s", render_id),
                         [("first night", 2, "Mind the step by the old door.", "Mind the step by the old door.", MUDDLE,
                           1.0, 2, report["lines"][1]["file"], None)])

    def test_rendered_again_it_takes_the_place_of_what_was_there(self):
        self.listener.hears = lambda text, seed: MUDDLE if "step" in text or "way" in text else text
        report = self.render("GRACE: I am on my way over.\nADA: Mind the step by the old door.", voices=None,
                             attempts=1, more_seeds=0)
        render_id = scene.record(report)
        self.query("UPDATE media_audio.review_queue SET verdict = 'accept', reviewed_at = now() "
                   "WHERE render_id = %s AND line_no = 1 RETURNING id", (render_id,))
        self.listener.hears = lambda text, seed: MUDDLE if "way" in text else text
        again = self.render("GRACE: I am on my way over.\nADA: Mind the step by the old door.\nADA: There.", voices=None,
                            attempts=1, more_seeds=0)
        self.assertEqual(scene.record(again), render_id)
        self.assertEqual(self.rows("SELECT count(*) FROM media_audio.renders"), [(1,)])
        self.assertEqual(self.rows("SELECT count(*) FROM media_audio.render_chunks WHERE render_id = %s", render_id), [(3,)])
        # The line somebody judged keeps its judgement beside the new take; the one that now passes is gone.
        self.assertEqual(self.rows("SELECT line_no, verdict FROM media_audio.review_queue WHERE render_id = %s "
                                   "ORDER BY created_at, verdict NULLS LAST", render_id), [(1, "accept"), (1, None)])

    def test_the_command_says_a_script_and_writes_it_down(self):
        self.listener.hears = lambda text, seed: MUDDLE if "step" in text else text
        script = self.script("[DING]\nGRACE: I am on my way over.\nADA: Mind the step by the old door.",
                             head=CAST + SOUNDS + 'WIND: { prompt: "wind over water" }\n')
        target = self.here / "out/night.flac"
        with mock.patch.object(scene, "speech_adapter", lambda engine, settings=None: self.speaker), \
                mock.patch.object(scene, "transcription_adapter", lambda settings=None: self.listener):
            status, out, err = run("say", str(script), str(target), "--stems", "--attempts", "1", "--label", "harbour")
            self.assertEqual((status, err), (0, ""))
            self.assertIn("[001] grace/neutral seed 507, 1 take, clean", out)
            self.assertIn(f"2 lines, {pcm.seconds(target):.1f} seconds: {target}", out)
            self.assertIn("1 need a listen: 2", out)
            self.assertTrue((self.here / "out/night.stems/dialogue.wav").is_file())
            self.assertEqual(self.rows("SELECT engine_tts FROM media_audio.renders WHERE label = 'harbour'"), [("stand-in",)])
            # Only one of them again, as loud as it comes, with the gap wider.
            self.speaker.asked.clear()
            status, out, _ = run("say", str(script), str(target), "--only", "ada", "--as-mixed", "--gap", "1",
                                 "--label", "harbour")
            self.assertEqual(status, 0)
            self.assertIn("[001] grace: kept as it was", out)
            self.assertTrue(all(voice.persona == "ada" for _, voice, _ in self.speaker.asked))
            self.assertEqual(self.rows("SELECT gap_seconds::float, params->>'loudness' FROM media_audio.renders "
                                       "WHERE label = 'harbour'"), [(1.0, None)])

    def test_the_command_reports_what_a_script_lacks(self):
        script = self.script("[WIND]\nBORIS: Is it.", head=CAST + 'BORIS: { persona: boris }\n@sounds\nWIND: { prompt: "wind" }\n')
        status, out, err = run("say", str(script), str(self.here / "out/night.flac"))
        self.assertEqual(status, 1)
        self.assertIn("2 problems", err)
        self.assertIn("`boris` has no voice", err)
        self.assertIn("--without-generated", err)
        status, out, _ = run("say", str(script), str(self.here / "out/night.flac"), "--without-generated")
        self.assertEqual(status, 1)


class Commands(unittest.TestCase):
    def test_what_cannot_be_asked_for_is_said_before_anything_is_done(self):
        status, _, err = run("say", "no-such-script.conv", "out.flac")
        self.assertEqual((status, err), (2, "me: no-such-script.conv: no such file\n"))
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / "night.conv"
            script.write_text("---\n[pause 1]\n")
            status, _, err = run("say", str(script), "out.flac", "--loudness", "-20", "--as-mixed")
        self.assertEqual((status, err), (2, "me: --loudness and --as-mixed cannot both be given\n"))


if __name__ == "__main__":
    unittest.main()
