"""Keeping house: every file the engine made, and the files it has no record of."""

import os
from pathlib import Path
from unittest import mock

from constructive_media import derive, ingest, outputs
from constructive_media.edit import render, timelines
from constructive_media.edit.timelines import Clip

from . import test_cli
from .support import TEST_DATABASE_VARIABLE, DatabaseTest, make_tone, settings_in
from .test_export import Shoot

NAME = "made-up-tide"


def squash(text):
    return [" ".join(line.split()) for line in text.splitlines()]


class OutputsTest(Shoot, DatabaseTest):
    def setUp(self):
        super().setUp()
        # A work folder of its own for each test: what one leaves lying about is not another's to find.
        self.settings = settings_in(self.root / "homes" / self._testMethodName)
        self.work = self.settings.work_dir
        ingest.ingest(self.footage, "made-up-quay", settings=self.settings)
        self.assets = {Path(path).stem: asset_id for asset_id, path in self.query(
            "SELECT id, path FROM media.assets WHERE path LIKE %s", (f"{self.footage}/%",))}
        environment = mock.patch.dict(os.environ, {
            "CONSTRUCTIVE_MEDIA_HOME": str(self.settings.home),
            "CONSTRUCTIVE_MEDIA_DATABASE_URL": os.environ[TEST_DATABASE_VARIABLE],
        })
        environment.start()
        self.addCleanup(environment.stop)
        timelines.save(NAME, [Clip(self.assets["quay cam"], 0.2, 1.0)], project="made-up-quay")

    def make(self):
        """A render of the cut, and a piece cut out of the sound recording."""
        cut = render.render(NAME, self.work / "renders" / "tide.mp4", settings=self.settings).path
        piece = make_tone(self.work / "snippets" / "gull.wav")
        derive.record(piece, "snippet", "made-up tool", [derive.Source(self.assets["lapel"], "cut from")])
        return cut, piece

    def stray(self, *parts):
        file = self.work.joinpath(*parts)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"made-up")
        return file

    def test_everything_made_is_listed_with_what_it_was_made_from(self):
        cut, piece = self.make()
        made = outputs.made()
        self.assertEqual([(one.path, one.kind, one.tool, one.sources, one.there) for one in made], [
            (cut, "render", "media render", ("quay cam.mov",), True),
            (piece, "snippet", "made-up tool", ("lapel.wav",), True),
        ])
        self.assertEqual(made[0].params["timeline"], NAME)
        self.assertAlmostEqual(made[0].duration_s, 0.8, delta=0.1)
        self.assertEqual([one.path for one in outputs.made("snippet")], [piece])
        self.assertEqual(outputs.made("export"), [])
        with self.assertRaisesRegex(ValueError, "unknown kind 'hologram'"):
            outputs.made("hologram")
        # A recording that was ingested was not made by the engine.
        self.assertNotIn(self.quay, [one.path for one in made])

        status, out, err = test_cli.run("outputs")
        self.assertEqual((status, err), (0, ""))
        first, second, *rest = squash(out)
        self.assertRegex(first, rf"^render 0:00\.\d {cut} from quay cam\.mov; the cut {NAME}$")
        self.assertEqual(second, f"snippet 0:00.5 {piece} from lapel.wav")
        self.assertEqual(rest, ["2 derived files on record", f"every picture and sound in {self.work} is on record"])

        # A file that is gone is still on record: the record is how to make it again.
        piece.unlink()
        status, out, _ = test_cli.run("outputs", "--kind", "snippet")
        self.assertEqual((status, squash(out)[:2]), (0, [
            f"snippet 0:00.5 {piece} from lapel.wav; no longer there",
            "1 snippet file on record, 1 no longer there",
        ]))
        self.assertEqual(squash(test_cli.run("outputs", "--kind", "still")[1])[0], "0 still files on record")

    def test_files_nothing_on_record_names_are_found(self):
        cut, piece = self.make()
        lapel = self.assets["lapel"]
        # What the engine keeps and names somewhere: a small copy, a voice's card, the clip a voice was made
        # from, a script said aloud and one line of it.
        ((proxy,),) = self.query("SELECT proxy_path FROM media.assets WHERE id = %s", (self.assets["quay cam"],))
        proxy = Path(proxy)
        self.assertEqual((proxy.parent, proxy.is_file()), (self.work / "proxies", True))
        card = self.stray("speakers", str(lapel)[:8], "voice1.wav")
        self.query("""INSERT INTO media_video.speaker_clusters (asset_id, label, method)
                      VALUES (%s, 'voice1', 'made-up') RETURNING id""", (lapel,))
        reference = self.stray("voices", "ada.wav")
        ((ada,),) = self.query("INSERT INTO media.personas (slug, name) VALUES ('ada-quay', 'Ada Quay') RETURNING id")
        self.query("""INSERT INTO media.voices (persona_id, direction, engine, profile_ref, reference_audio_path)
                      VALUES (%s, 'synthesis', 'made-up', 'ada', %s) RETURNING id""", (ada, str(reference)))
        said = self.stray("said", "harbour.wav")
        line = self.stray("said", "harbour", "line-001.wav")
        ((spoken,),) = self.query(
            """INSERT INTO media_audio.renders (label, output_path, engine_tts)
               VALUES ('made-up-harbour', %s, 'made-up') RETURNING id""", (str(said),))
        self.query("""INSERT INTO media_audio.render_chunks (render_id, line_no, text, audio_path)
                      VALUES (%s, 1, 'made-up words', %s) RETURNING id""", (spoken, str(line)))
        self.assertTrue({cut, piece, proxy, card, reference, said, line, self.quay} <= outputs.recorded(self.settings))
        self.assertEqual(outputs.unrecorded(self.work, self.settings), [])

        # What nobody can account for: an old render, a card for a voice no longer there, a loose sound file.
        loose = self.stray("loose.WAV")
        old = self.stray("renders", "old cut.mp4")
        ghost = self.stray("speakers", str(lapel)[:8], "voice9.wav")
        # And what is not looked at: stills, pages, and anything kept out of sight.
        self.stray("frames", str(lapel)[:8], "0001.jpg")
        self.stray("notes.txt")
        self.stray(".cache", "hidden.wav")
        self.assertEqual(outputs.unrecorded(self.work, self.settings), [loose, old, ghost])
        self.assertEqual(outputs.unrecorded(self.work / "renders", self.settings), [old])

        status, out, err = test_cli.run("outputs")
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(squash(out)[2:], [
            "2 derived files on record",
            f"3 files in {self.work} with no record of what they were made from:",
            f"0.0 MB {loose}", f"0.0 MB {old}", f"0.0 MB {ghost}",
        ])
        status, out, _ = test_cli.run("outputs", "--kind", "export", "--in", str(self.work / "renders"))
        self.assertEqual((status, squash(out)), (0, [
            "0 export files on record",
            f"1 file in {self.work / 'renders'} with no record of what it was made from:",
            f"0.0 MB {old}",
        ]))
        # Nothing was deleted, and looking wrote nothing down.
        self.assertTrue(all(file.is_file() for file in (loose, old, ghost)))
        self.assertEqual(len(outputs.made()), 2)

    def test_another_folder_can_be_looked_through(self):
        status, out, err = test_cli.run("outputs", "--in", str(self.footage))
        self.assertEqual((status, squash(out), err), (0, [
            "0 derived files on record", f"every picture and sound in {self.footage} is on record"], ""))
        missing = self.root / "made-up-nowhere"
        self.assertEqual(test_cli.run("outputs", "--in", str(missing)),
                         (1, "", f"media: {missing} is not a folder\n"))
        with self.assertRaises(NotADirectoryError):
            outputs.unrecorded(missing, self.settings)

    def test_a_work_folder_that_is_not_there_yet_has_nothing_to_look_through(self):
        home = self.root / "homes" / "made-up-new"
        with mock.patch.dict(os.environ, {"CONSTRUCTIVE_MEDIA_HOME": str(home)}):
            status, out, err = test_cli.run("outputs")
        self.assertEqual((status, squash(out), err), (0, [
            "0 derived files on record", f"{home / 'work'} is not there yet: nothing to look through"], ""))
        self.assertFalse(home.exists())
