"""Libraries of voices, sound banks and faces.

Every library here is written by the test, of made-up people. Its clips hold
no sound unless a test measures them, and then the sound is made-up talk.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from constructive_media import indexes, libraries, speech
from constructive_media.libraries import LibraryError

from .support import HAVE_FFMPEG, HAVE_NUMPY, DatabaseTest, make_render, make_tone, needs_ffmpeg, settings_in, syllables
from .test_cli import run

SAID = "The tide tables are kept in the drawer under the lamp."
OWN = {"by": "Ada Marsh", "kind": "own", "on": "2026-11-26"}
HAVE_GIT = shutil.which("git") is not None


def git(folder, *arguments):
    done = subprocess.run(["git", "-c", "user.name=Tester", "-c", "user.email=tester@example.com",
                           "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main", *arguments],
                          cwd=folder, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def write_library(folder: Path, name="lighthouse", **changes) -> Path:
    """A small library that is sound: one cloned voice, one of a server's own, a bank, a sound and a face."""
    for made in ("voices/ada", "banks", "sounds", "faces"):
        (folder / made).mkdir(parents=True, exist_ok=True)
    (folder / "voices/ada/neutral.flac").write_bytes(b"a clip, were it sound")
    (folder / "voices/ada/neutral.txt").write_text(SAID + "\n", encoding="utf-8")
    (folder / "sounds/rain.flac").write_bytes(b"rain, were it sound")
    (folder / "faces/ada.png").write_bytes(b"a face, were it a picture")
    (folder / "banks/weather.sounds").write_text("@sounds\nRAIN: { file: rain.flac, loop: true, track: ambience }\n",
                                                 encoding="utf-8")
    manifest = {
        "library": name, "version": "0.1.0", "license": "CC-BY-4.0",
        "voices": [
            {"persona": "ada", "name": "Ada Marsh", "reference": "voices/ada/neutral.flac", "seed": 7,
             "consent": OWN},
            {"persona": "ben", "emotion": "whisper", "profile": "a-quiet-voice",
             "consent": {"by": "nobody", "kind": "synthetic", "note": "one of the speech server's own"}},
        ],
        "banks": ["banks"], "sounds": ["sounds"], "faces": {"ada": "faces/ada.png"},
        **changes,
    }
    (folder / libraries.MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return folder


class InFolder(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name).resolve()
        self.settings = settings_in(self.folder / "home")

    def problems(self, **changes):
        folder = self.folder / f"library-{len(list(self.folder.iterdir()))}"
        return list(libraries.check(write_library(folder, **changes), measure=False).problems)

    def voice(self, **changes):
        voice = {"persona": "ada", "reference": "voices/ada/neutral.flac", "consent": OWN, **changes}
        return [{key: value for key, value in voice.items() if value is not None}]


class CheckTest(InFolder):
    def test_a_sound_library_is_read(self):
        checked = libraries.check(write_library(self.folder / "library"), measure=False)
        self.assertEqual((checked.problems, checked.notes), ((), ()))
        library = checked.library
        self.assertEqual((library.name, library.version, library.license), ("lighthouse", "0.1.0", "CC-BY-4.0"))
        ada, ben = library.voices
        self.assertEqual((ada.persona, ada.emotion, ada.name, ada.said, ada.seed, ada.consent),
                         ("ada", "neutral", "Ada Marsh", SAID, 7, OWN))
        self.assertEqual(ada.reference, self.folder / "library/voices/ada/neutral.flac")
        self.assertEqual((ben.persona, ben.emotion, ben.reference, ben.profile), ("ben", "whisper", None, "a-quiet-voice"))
        self.assertEqual(library.banks, (self.folder / "library/banks",))
        self.assertEqual(library.sounds, (self.folder / "library/sounds",))
        self.assertEqual(library.faces, {"ada": self.folder / "library/faces/ada.png"})
        self.assertEqual(libraries.load(self.folder / "library"), library)

    def test_a_folder_that_is_no_library_is_told(self):
        self.assertIn("has no media-library.json", libraries.check(self.folder).problems[0])
        (self.folder / libraries.MANIFEST).write_text("{ not json", encoding="utf-8")
        self.assertIn("cannot be read as JSON", libraries.check(self.folder).problems[0])
        (self.folder / libraries.MANIFEST).write_text("[]", encoding="utf-8")
        self.assertIn("should hold an object", libraries.check(self.folder).problems[0])
        with self.assertRaises(LibraryError) as raised:
            libraries.load(self.folder)
        self.assertIn("cannot be used as a library", str(raised.exception))

    def test_every_voice_says_by_what_right_it_is_there(self):
        (problem,) = self.problems(voices=self.voice(consent=None))
        self.assertIn('ada/neutral: it has no "consent"', problem)
        self.assertIn("own | permission | licensed | synthetic", problem)
        self.assertIn('does not say who gave it, as "by"', self.problems(voices=self.voice(consent={"kind": "own"}))[0])
        self.assertIn("should be one of own, permission, licensed, synthetic",
                      self.problems(voices=self.voice(consent={"by": "Ada Marsh", "kind": "asked nicely"}))[0])
        self.assertIn("which license, and where the recording is from",
                      self.problems(voices=self.voice(consent={"by": "an archive", "kind": "licensed"}))[0])
        self.assertIn("how the voice was made",
                      self.problems(voices=self.voice(consent={"by": "nobody", "kind": "synthetic"}))[0])
        self.assertIn("should be written like 2026-11-26",
                      self.problems(voices=self.voice(consent={**OWN, "on": "last spring"}))[0])
        self.assertEqual(self.problems(voices=self.voice(consent={"by": "Ada Marsh", "kind": "permission"})), [])

    def test_what_is_wrong_with_a_voice_is_said(self):
        self.assertIn("its clip, `voices/ada/gone.flac`, is not there",
                      self.problems(voices=self.voice(reference="voices/ada/gone.flac"))[0])
        self.assertIn("is not a kind of sound file", self.problems(voices=self.voice(reference="voices/ada/neutral.txt"))[0])
        self.assertIn('a "reference", a clip to clone from, or a "profile"',
                      self.problems(voices=self.voice(reference=None))[0])
        self.assertIn("lower case, with hyphens", self.problems(voices=self.voice(persona="Ada Marsh"))[0])
        self.assertIn("is there twice", self.problems(voices=self.voice() + self.voice())[0])
        self.assertIn('"seed" should be a whole number', self.problems(voices=self.voice(seed="seven"))[0])
        self.assertIn('"voices" should be a list', self.problems(voices={"ada": {}})[0])
        self.assertIn("lower case letters and numbers", self.problems(library="Light House")[0])

    def test_the_words_of_a_clip_are_in_the_manifest_or_beside_it(self):
        folder = write_library(self.folder / "library")
        (folder / "voices/ada/neutral.txt").unlink()
        (problem,) = libraries.check(folder, measure=False).problems
        self.assertIn("the words said in its clip are not written down", problem)
        self.assertIn("voices/ada/neutral.txt", problem)
        self.assertEqual(self.problems(voices=self.voice(said="Said here instead.")), [])

    def test_nothing_a_library_names_is_outside_it(self):
        (self.folder / "outside.flac").write_bytes(b"")
        for path in ("../outside.flac", str(self.folder / "outside.flac")):
            self.assertIn("is outside the library", self.problems(voices=self.voice(reference=path))[0])
        self.assertIn("is outside the library", self.problems(banks=[".."])[0])
        self.assertIn("is outside the library", self.problems(faces={"ada": "../ada.png"})[0])

    def test_banks_are_read_and_the_sounds_they_play_are_looked_for(self):
        self.assertIn('"banks" names `tunes`, and there is no such folder', self.problems(banks=["banks", "tunes"])[0])
        (problem,) = self.problems(sounds=[])
        self.assertIn("bank `weather`: `RAIN` plays `rain.flac`, which is not in the library's sounds", problem)
        folder = write_library(self.folder / "broken")
        (folder / "banks/weather.sounds").write_text("@sounds\nRAIN: { file: /tmp/rain.flac }\nWIND: { blows: yes }\n")
        problems = libraries.check(folder, measure=False).problems
        self.assertIn("unknown key `blows`", problems[0])
        (folder / "banks/weather.sounds").write_text("@sounds\nRAIN: { file: /tmp/rain.flac }\n")
        self.assertIn("a path only this machine has", libraries.check(folder, measure=False).problems[0])

    def test_faces_are_pictures_that_are_there(self):
        self.assertIn("the face of ada, `faces/gone.png`, is not there", self.problems(faces={"ada": "faces/gone.png"})[0])
        self.assertIn("should be a picture", self.problems(faces={"ada": "voices/ada/neutral.flac"})[0])

    def test_what_is_only_worth_knowing_is_a_note(self):
        folder = write_library(self.folder / "library", colour="teal")
        (folder / "voices/ada/neutral.flac").write_bytes(b"\0" * (libraries.CLIP_BYTES + 1))
        checked = libraries.check(folder, measure=False)
        self.assertEqual(checked.problems, ())
        self.assertIn('"colour" is not something a library says', checked.notes[0])
        self.assertIn("the recording it was cut from is kept somewhere else", checked.notes[1])
        empty = self.folder / "empty"
        empty.mkdir()
        (empty / libraries.MANIFEST).write_text('{"library": "empty"}')
        self.assertEqual(libraries.check(empty).notes, ("it holds no voices, no banks and no faces yet",))

    @unittest.skipUnless(HAVE_FFMPEG and HAVE_NUMPY, "ffmpeg and numpy are needed")
    def test_clips_are_measured(self):
        from constructive_media.audio import pcm

        folder = write_library(self.folder / "library")
        pcm.write(folder / "voices/ada/neutral.wav", syllables(40.0), 24_000, bits=16)
        (folder / "voices/ada/neutral.flac").unlink()
        manifest = json.loads((folder / libraries.MANIFEST).read_text())
        manifest["voices"][0]["reference"] = "voices/ada/neutral.wav"
        (folder / libraries.MANIFEST).write_text(json.dumps(manifest))
        (problem,) = libraries.check(folder).problems
        self.assertIn("ada/neutral: it runs 40.0 seconds", problem)
        pcm.write(folder / "voices/ada/neutral.wav", syllables(13.0), 24_000, bits=16)
        checked = libraries.check(folder)
        self.assertEqual(checked.problems, ())
        self.assertIn("ada/neutral: it runs 13.0 seconds; 15 to 28 is what clones best", checked.notes)

    def test_the_command_says_what_it_found(self):
        folder = write_library(self.folder / "library")
        if HAVE_FFMPEG:   # the command measures every clip it can, so this one has to be sound
            make_tone(folder / "voices/ada/neutral.flac", 16.0)
        status, out, _ = run("library", "check", str(folder))
        self.assertEqual((status, out.splitlines()[-1]), (0, "ok: lighthouse, 2 voices, 1 folder of banks, 1 face"))
        status, out, _ = run("library", "check", str(folder), "--json")
        self.assertEqual(json.loads(out)["voices"][0],
                         {"persona": "ada", "emotion": "neutral", "reference": "voices/ada/neutral.flac",
                          "profile": None, "consent": OWN})
        (folder / "faces/ada.png").unlink()
        status, out, err = run("library", "check", str(folder))
        self.assertEqual(status, 1)
        self.assertIn("the face of ada", out)
        self.assertIn("1 problem: this is not a library yet", err)
        self.assertIn("library", run("--help")[1])


class AddedTest(InFolder):
    def test_a_folder_is_used_where_it_is(self):
        folder = write_library(self.folder / "library")
        self.assertEqual(libraries.added(self.settings), {})
        one, checked = libraries.add(str(folder), settings=self.settings)
        self.assertEqual((one.name, one.folder, one.linked, one.commit), ("lighthouse", folder, True, None))
        self.assertEqual(len(checked.library.voices), 2)
        self.assertEqual(libraries.added(self.settings), {"lighthouse": one})
        self.assertEqual(libraries.banks(self.settings), [folder / "banks"])
        self.assertEqual(libraries.sounds(self.settings), [folder / "sounds"])
        self.assertEqual(libraries.faces(self.settings), {"ada": folder / "faces/ada.png"})
        # Added again it is the same one; another of the same name is refused.
        libraries.add(str(folder), settings=self.settings)
        with self.assertRaises(LibraryError) as raised:
            libraries.add(str(write_library(self.folder / "another")), settings=self.settings)
        self.assertIn("a library called `lighthouse` was added already", str(raised.exception))
        self.assertEqual(libraries.remove("lighthouse", settings=self.settings).name, "lighthouse")
        self.assertTrue(folder.is_dir())
        self.assertEqual(libraries.added(self.settings), {})

    def test_a_library_with_problems_is_not_added_and_one_that_has_gone_is_passed_over(self):
        folder = write_library(self.folder / "library")
        (folder / "faces/ada.png").unlink()
        with self.assertRaises(LibraryError) as raised:
            libraries.add(str(folder), settings=self.settings)
        self.assertIn("was not added", str(raised.exception))
        self.assertIn("the face of ada", str(raised.exception))
        libraries.add(str(write_library(self.folder / "library")), settings=self.settings)
        shutil.rmtree(folder)
        self.assertEqual((libraries.banks(self.settings), libraries.faces(self.settings)), ([], {}))
        for call in (libraries.remove, libraries.update):
            with self.assertRaises(LibraryError) as raised:
                call("harbour", settings=self.settings)
            self.assertIn("no library called `harbour` was added. Added: lighthouse", str(raised.exception))

    @unittest.skipUnless(HAVE_GIT, "git is needed")
    def test_an_address_is_cloned_and_kept_at_its_commit(self):
        origin = write_library(self.folder / "origin")
        git(origin, "init", "--quiet")
        git(origin, "add", "--all")
        git(origin, "commit", "--quiet", "--message", "a library")
        first = git(origin, "rev-parse", "HEAD")
        one, _ = libraries.add(f"file://{origin}", settings=self.settings)
        kept = self.folder / "home/libraries/lighthouse"
        self.assertEqual((one.folder, one.linked, one.commit, one.source), (kept, False, first, f"file://{origin}"))
        self.assertTrue((kept / "voices/ada/neutral.flac").is_file())
        self.assertEqual([path.name for path in (self.folder / "home/libraries").iterdir()], ["lighthouse"])
        with self.assertRaises(LibraryError) as raised:
            libraries.add(f"file://{origin}", settings=self.settings)
        self.assertIn("media library update lighthouse", str(raised.exception))

        # The origin moves on: the library stays where it was until it is brought up to date.
        (origin / "banks/harbour.sounds").write_text("@sounds\nHORN: { file: rain.flac }\n")
        git(origin, "add", "--all")
        git(origin, "commit", "--quiet", "--message", "a horn")
        second = git(origin, "rev-parse", "HEAD")
        self.assertFalse((kept / "banks/harbour.sounds").exists())
        lock = self.folder / "project" / libraries.LOCK
        lock.parent.mkdir()
        self.assertEqual(libraries.lock(lock, settings=self.settings),
                         {"lighthouse": {"source": f"file://{origin}", "commit": first}})
        one, before = libraries.update("lighthouse", settings=self.settings)
        self.assertEqual((one.commit, before), (second, first))
        self.assertTrue((kept / "banks/harbour.sounds").is_file())

        # What a project wrote down is what is installed: here, and on a machine that has nothing.
        said = []
        libraries.install(lock, settings=self.settings, tell=said.append)
        self.assertEqual((libraries.added(self.settings)["lighthouse"].commit, said), (first, [f"brought lighthouse to {first[:12]}"]))
        libraries.install(lock, settings=self.settings, tell=said.append)
        self.assertEqual(said[-1], "lighthouse is here already")
        elsewhere = settings_in(self.folder / "elsewhere")
        (there,) = libraries.install(lock, settings=elsewhere)
        self.assertEqual((there.commit, there.folder), (first, self.folder / "elsewhere/libraries/lighthouse"))

        # One that would be brought to something broken is left where it was.
        (origin / "faces/ada.png").unlink()
        git(origin, "add", "--all")
        git(origin, "commit", "--quiet", "--message", "a face lost")
        with self.assertRaises(LibraryError) as raised:
            libraries.update("lighthouse", settings=self.settings)
        self.assertIn("was left as it was", str(raised.exception))
        self.assertEqual(git(kept, "rev-parse", "HEAD"), first)
        libraries.remove("lighthouse", settings=self.settings)
        self.assertFalse(kept.exists())

    @unittest.skipUnless(HAVE_GIT, "git is needed")
    def test_what_is_not_a_library_or_not_a_repository_is_not_added(self):
        origin = self.folder / "origin"
        origin.mkdir()
        (origin / "README.md").write_text("Not a library.\n")
        git(origin, "init", "--quiet")
        git(origin, "add", "--all")
        git(origin, "commit", "--quiet", "--message", "not a library")
        with self.assertRaises(LibraryError) as raised:
            libraries.add(f"file://{origin}", settings=self.settings)
        self.assertIn("has no media-library.json", str(raised.exception))
        with self.assertRaises(LibraryError) as raised:
            libraries.add(f"file://{self.folder / 'nowhere'}", settings=self.settings)
        self.assertIn("git could not clone", str(raised.exception))
        with self.assertRaises(LibraryError):
            libraries.add("--upload-pack=echo", settings=self.settings)
        self.assertEqual(list((self.folder / "home/libraries").iterdir()), [])
        self.assertEqual(libraries.added(self.settings), {})

    def test_a_project_file_that_cannot_be_read_is_told(self):
        with self.assertRaises(LibraryError) as raised:
            libraries.install(self.folder / libraries.LOCK, settings=self.settings)
        self.assertIn("`media library lock` writes one", str(raised.exception))
        (self.folder / libraries.LOCK).write_text('{"libraries": []}')
        with self.assertRaises(LibraryError) as raised:
            libraries.install(self.folder / libraries.LOCK, settings=self.settings)
        self.assertIn("should hold", str(raised.exception))
        # A folder is written from where the file is.
        write_library(self.folder / "library")
        (self.folder / libraries.LOCK).write_text('{"libraries": {"lighthouse": {"source": "library"}}}')
        (one,) = libraries.install(self.folder / libraries.LOCK, settings=self.settings)
        self.assertEqual((one.folder, one.linked), (self.folder / "library", True))

    def test_a_script_finds_the_banks_of_a_library_after_its_own(self):
        from constructive_media import conv

        libraries.add(str(write_library(self.folder / "library")), settings=self.settings)
        script = self.folder / "scene" / "scene.conv"
        script.parent.mkdir()
        script.write_text("@include weather\n@characters\nADA: { persona: ada }\n---\n[RAIN]\nADA: It rains.\n")
        with self.assertRaises(conv.ConvError):
            conv.load(script)
        shared = libraries.banks(self.settings)
        scene = conv.load(script, banks=[*conv.bank_folders(script), *shared])
        self.assertEqual(scene["sounds"]["RAIN"]["file"], "rain.flac")
        (script.parent / "banks").mkdir()
        (script.parent / "banks/weather.sounds").write_text("@sounds\nRAIN: { file: my-own-rain.flac }\n")
        scene = conv.load(script, banks=[*conv.bank_folders(script), *shared])
        self.assertEqual(scene["sounds"]["RAIN"]["file"], "my-own-rain.flac")
        self.assertEqual(conv.bank_folders(script, ["given"]), ["given"])

    @needs_ffmpeg
    def test_a_film_finds_the_faces_of_a_library_for_whoever_has_none(self):
        from constructive_media.stage import timeline

        from .support import make_still

        library = write_library(self.folder / "library", faces={"ada": "faces/ada.png", "grace": "faces/ada.png"})
        make_still(library / "faces/ada.png")
        libraries.add(str(library), settings=self.settings)
        mine = make_still(self.folder / "faces" / "grace.jpg").parent
        made = timeline.build(make_render(self.folder / "render"), self.folder / "staged", faces=mine,
                              more_faces=libraries.faces(self.settings))
        self.assertEqual(made["faces"], {"ada": "faces/ada.png", "grace": "faces/grace.jpg"})
        self.assertEqual((self.folder / "staged/faces/ada.png").read_bytes(), (library / "faces/ada.png").read_bytes())


def write_index(folder: Path, *listed, name="harbour") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / indexes.INDEX).write_text(json.dumps({"index": name, "libraries": list(listed)}, indent=2),
                                        encoding="utf-8")
    return folder


class IndexTest(InFolder):
    LISTED = {"name": "lighthouse", "source": "harbour-office/lighthouse-voices", "license": "CC-BY-4.0",
              "summary": "The keepers of the light, and the weather they keep it in.",
              "holds": ["voices", "banks"], "tags": ["Weather"]}

    def origin(self, name="lighthouse"):
        origin = write_library(self.folder / f"origin-{name}", name=name)
        git(origin, "init", "--quiet")
        git(origin, "add", "--all")
        git(origin, "commit", "--quiet", "--message", "a library")
        return origin

    def reading(self, *listed, **changes):
        return settings_in(self.folder / "home", indexes=(str(write_index(self.folder / "index", *listed, **changes)),))

    def test_an_index_is_read(self):
        checked = indexes.check(write_index(self.folder / "index", self.LISTED, {"name": "tides", "source": "https://example.com/tides.git"}))
        self.assertEqual((checked.name, checked.problems), ("harbour", ()))
        first, second = checked.listed
        self.assertEqual((first.name, first.source, first.license, first.holds, first.tags, first.index),
                         ("lighthouse", "harbour-office/lighthouse-voices", "CC-BY-4.0", ("voices", "banks"),
                          ("weather",), "harbour"))
        self.assertEqual((second.summary, second.holds), (None, ()))
        self.assertEqual(checked.notes, ("`tides` has no summary, so a search finds it only by its name",))
        self.assertEqual(indexes.check(self.folder).problems, (f"{self.folder} is not an index: it has no media-index.json",))
        self.assertEqual(indexes.check(write_index(self.folder / "empty")).notes, ("it lists no libraries yet",))

    def test_what_is_wrong_with_an_index_is_said(self):
        def problems(*listed, **changes):
            return list(indexes.check(write_index(self.folder / "index", *listed, **changes)).problems)

        self.assertIn('"index" is the index\'s name', problems(name="The Harbour")[0])
        self.assertIn("is listed twice", problems(self.LISTED, self.LISTED)[0])
        self.assertIn('"name" is what the library calls itself', problems({**self.LISTED, "name": "Light House"})[0])
        self.assertIn("`sea`. A library holds any of voices, banks, faces", problems({**self.LISTED, "holds": ["sea"]})[0])
        self.assertIn('"tags" should be a list of words', problems({**self.LISTED, "tags": "weather"})[0])
        # An index is somebody else's file: it names an address to fetch, and nothing that is a path,
        # an option or a program to run.
        for source in ("../lighthouse", "/srv/lighthouse", "lighthouse", "--upload-pack=touch x",
                       "ext::sh -c 'touch x'", "helper::https://example.com/x", "https://example.com/a b", "", 7):
            self.assertIn('"source" is where the library is', problems({**self.LISTED, "source": source})[0], source)
        for source in ("owner/name", "https://example.com/x.git", "ssh://git@example.com/x.git", "git@example.com:x.git"):
            self.assertEqual(problems({**self.LISTED, "source": source}), [], source)

    def test_libraries_are_looked_for_by_any_words(self):
        settings = self.reading(self.LISTED, {"name": "tides", "source": "owner/tides", "summary": "High water, by the hour.",
                                              "holds": ["banks"]})
        names = lambda *words: [one.name for one in indexes.search(words, settings)]
        self.assertEqual(names(), ["lighthouse", "tides"])
        self.assertEqual((names("WEATHER"), names("banks"), names("voices"), names("banks", "hour"), names("fog")),
                         (["lighthouse"], ["lighthouse", "tides"], ["lighthouse"], ["tides"], []))
        self.assertEqual(indexes.find("tides", settings).source, "owner/tides")
        self.assertIsNone(indexes.find("fog", settings))

    def test_the_first_index_to_name_a_library_is_believed_and_one_that_cannot_be_read_is_passed_over(self):
        first = write_index(self.folder / "first", {**self.LISTED, "source": "first/lighthouse"})
        second = write_index(self.folder / "second", self.LISTED, {"name": "tides", "source": "owner/tides"})
        broken = write_index(self.folder / "broken", name="Not A Name")
        said = []
        settings = settings_in(self.folder / "home", indexes=(str(broken), "not an address", str(first), str(second)))
        found = indexes.listed(settings, tell=said.append)
        self.assertEqual([(one.name, one.source) for one in found],
                         [("lighthouse", "first/lighthouse"), ("tides", "owner/tides")])
        self.assertIn("was passed over", said[0])
        self.assertIn("neither a folder nor an address", said[1])
        self.assertEqual(indexes.listed(settings_in(self.folder / "home")), [])

    @unittest.skipUnless(HAVE_GIT, "git is needed")
    def test_a_library_is_added_by_the_name_an_index_lists_it_under(self):
        origin = self.origin()
        settings = self.reading({**self.LISTED, "source": f"file://{origin}"},
                                {"name": "tides", "source": f"file://{origin}"})
        one, checked = libraries.add("lighthouse", settings=settings)
        self.assertEqual((one.name, one.source, one.linked, one.commit),
                         ("lighthouse", f"file://{origin}", False, git(origin, "rev-parse", "HEAD")))
        self.assertEqual(len(checked.library.voices), 2)
        # What a project writes down is the address, so the same libraries install where no index is read.
        self.assertEqual(libraries.lock(self.folder / "lock.json", settings=settings)["lighthouse"]["source"],
                         f"file://{origin}")
        with self.assertRaises(LibraryError) as raised:
            libraries.add("fog", settings=settings)
        self.assertIn("no index lists a library called `fog`", str(raised.exception))
        self.assertIn("media library search", str(raised.exception))
        # An index says where a library is. What is there has to call itself the same.
        with self.assertRaises(LibraryError) as raised:
            libraries.add("tides", settings=settings)
        self.assertIn("the index lists `tides`", str(raised.exception))
        self.assertIn("calls itself `lighthouse`", str(raised.exception))
        self.assertEqual(list(libraries.added(settings)), ["lighthouse"])
        with self.assertRaises(LibraryError) as raised:
            libraries.add("lighthouse", settings=settings_in(self.folder / "elsewhere", indexes=("owner/nowhere-at-all",)))
        self.assertIn("no index lists", str(raised.exception))

    @unittest.skipUnless(HAVE_GIT, "git is needed")
    def test_an_index_at_an_address_is_kept_and_looked_at_again_when_asked(self):
        origin = write_index(self.folder / "origin-index", self.LISTED)
        git(origin, "init", "--quiet")
        git(origin, "add", "--all")
        git(origin, "commit", "--quiet", "--message", "an index")
        settings = settings_in(self.folder / "home", indexes=(f"file://{origin}",))
        self.assertEqual([one.name for one in indexes.listed(settings)], ["lighthouse"])
        (kept,) = (self.folder / "home/indexes").iterdir()
        self.assertTrue((kept / indexes.INDEX).is_file())
        write_index(origin, self.LISTED, {"name": "tides", "source": "owner/tides"})
        git(origin, "commit", "--quiet", "--all", "--message", "tides")
        self.assertEqual([one.name for one in indexes.listed(settings)], ["lighthouse"])            # within the day
        self.assertEqual([one.name for one in indexes.listed(settings, refresh=True)], ["lighthouse", "tides"])
        # The address gone, what was fetched before is still an index.
        shutil.rmtree(origin)
        self.assertEqual([one.name for one in indexes.listed(settings, refresh=True)], ["lighthouse", "tides"])
        said = []
        elsewhere = settings_in(self.folder / "elsewhere", indexes=(f"file://{origin}",))
        self.assertEqual(indexes.listed(elsewhere, tell=said.append), [])
        self.assertIn("could not be reached", said[0])
        self.assertIn("A library can still be added by its own address", said[0])
        self.assertEqual(list((self.folder / "elsewhere/indexes").iterdir()), [])

    @unittest.skipUnless(HAVE_GIT, "git is needed")
    def test_an_address_that_would_run_a_program_is_never_fetched(self):
        mark = self.folder / "ran"
        for address in (f"ext::sh -c 'touch {mark}'", f"ext::touch {mark}"):
            with self.assertRaises(LibraryError):
                libraries.add(address, settings=self.settings)
            lock = self.folder / libraries.LOCK
            lock.write_text(json.dumps({"libraries": {"lighthouse": {"source": address, "commit": None}}}))
            with self.assertRaises(LibraryError):
                libraries.install(lock, settings=self.settings)
        self.assertFalse(mark.exists())

    def test_the_command_looks_and_checks(self):
        index = write_index(self.folder / "index", self.LISTED, {"name": "tides", "source": "owner/tides"})
        status, out, _ = run("library", "check", str(index))
        self.assertEqual((status, out.splitlines()[-1]), (0, "ok: the index harbour, 2 libraries"))
        write_index(self.folder / "bad", {**self.LISTED, "source": "../lighthouse"})
        status, out, err = run("library", "check", str(self.folder / "bad"))
        self.assertEqual(status, 1)
        self.assertIn("1 problem: this is not an index yet", err)
        with mock.patch.dict(os.environ, {"CONSTRUCTIVE_MEDIA_LIBRARY_INDEX": str(index),
                                          "CONSTRUCTIVE_MEDIA_HOME": str(self.folder / "home")}):
            status, out, _ = run("library", "search")
            self.assertEqual(out.splitlines(), [
                "lighthouse  (voices and banks, CC-BY-4.0)",
                "    The keepers of the light, and the weather they keep it in.",
                "    harbour-office/lighthouse-voices",
                "tides",
                "    owner/tides",
                "add one with: media library add NAME"])
            self.assertEqual(run("library", "search", "weather", "--json")[1].count('"name"'), 1)
            self.assertEqual(run("library", "search", "fog", "light")[1], "nothing is listed with fog and light in it\n")
        with mock.patch.dict(os.environ, {"CONSTRUCTIVE_MEDIA_LIBRARY_INDEX": "none"}):
            self.assertIn("this machine reads no index", run("library", "search")[1])


class VoicesTest(DatabaseTest):
    def setUp(self):
        super().setUp()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name).resolve()
        self.settings = settings_in(self.folder / "home")
        self.library = write_library(self.folder / "library")
        libraries.add(str(self.library), settings=self.settings)

    def test_the_personas_here_are_given_the_voices_of_a_library(self):
        said = []
        done = libraries.voices("lighthouse", settings=self.settings, tell=said.append)
        self.assertEqual(done, {"given": ["ada/neutral", "ben/whisper"], "kept": []})
        self.assertEqual(said, ["  ada/neutral, from voices/ada/neutral.flac", "  ben/whisper, the server's `a-quiet-voice`"])
        ada = speech.StoredVoices().find("ada")
        self.assertEqual((ada.profile, ada.reference_audio, ada.reference_text, ada.seed),
                         ("ada", str(self.library / "voices/ada/neutral.flac"), SAID, 7))
        self.assertEqual(speech.StoredVoices().find("ben", "whisper").profile, "a-quiet-voice")
        # Given again, they are the library's own and are written over.
        self.assertEqual(libraries.voices("lighthouse", settings=self.settings)["kept"], [])

    def test_a_persona_with_a_voice_from_elsewhere_keeps_it_unless_told(self):
        speech.keep(speech.Voice("ada", profile="my-own-ada"), new=True)
        done = libraries.voices("lighthouse", settings=self.settings)
        self.assertEqual(done, {"given": ["ben/whisper"], "kept": ["ada/neutral"]})
        self.assertEqual(speech.StoredVoices().find("ada").profile, "my-own-ada")
        done = libraries.voices("lighthouse", only=["ada"], replace=True, settings=self.settings)
        self.assertEqual(done, {"given": ["ada/neutral"], "kept": []})
        self.assertEqual(speech.StoredVoices().find("ada").profile, "ada")
        with self.assertRaises(LibraryError) as raised:
            libraries.voices("lighthouse", only=["cy"], settings=self.settings)
        self.assertIn("has no voice for cy. It has: ada, ben", str(raised.exception))
