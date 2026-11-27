"""A cut handed over: the export with a title, a preview and the renders beside it."""

import os
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree

from constructive_media import derive, ffmpeg, ingest
from constructive_media.edit import EditError, handover, render, timelines
from constructive_media.edit.timelines import Clip

from . import test_cli
from .support import TEST_DATABASE_VARIABLE, DatabaseTest
from .test_export import Shoot
from .test_render import over

NAME = "made-up-quay-cut"


class HandoverTest(Shoot, DatabaseTest):
    def setUp(self):
        super().setUp()
        ingest.ingest(self.footage, "made-up-quay", settings=self.settings)
        self.assets = {Path(path).stem: asset_id for asset_id, path in self.query(
            "SELECT id, path FROM media.assets WHERE path LIKE %s", (f"{self.footage}/%",))}
        self.out = self.root / "handed" / self._testMethodName
        self.made = self.root / "made" / self._testMethodName
        environment = mock.patch.dict(os.environ, {
            "CONSTRUCTIVE_MEDIA_HOME": str(self.settings.home),
            "CONSTRUCTIVE_MEDIA_DATABASE_URL": os.environ[TEST_DATABASE_VARIABLE],
        })
        environment.start()
        self.addCleanup(environment.stop)
        timelines.save(NAME, [
            Clip(self.assets["quay cam"], 0.2, 1.0, label="the tide"),
            over(self.assets["plain"], 0.0, 0.8, self.assets["lapel"], 1.0),
        ], project="made-up-quay", title="The quay")

    def render(self, file):
        return render.render(NAME, file, settings=self.settings).path

    def age(self, file):
        """Make a render one that was made before the cut was last saved."""
        self.query("""UPDATE media.derivations d SET created_at = d.created_at - interval '1 hour'
                        FROM media.assets a WHERE a.id = d.output_id AND a.path = %s RETURNING d.id""", (str(file),))

    def test_a_title_can_be_given_for_the_editing_program(self):
        status, out, err = test_cli.run("export", NAME, "--to", str(self.out), "--title", "  Low  water ")
        self.assertEqual((status, err), (0, ""))
        self.assertEqual((self.out / f"{NAME}.edl").read_text().splitlines()[0], "TITLE: Low water")
        root = ElementTree.fromstring((self.out / f"{NAME}.fcpxml").read_text())
        self.assertEqual(root.find("library/event/project").get("name"), "Low water")
        self.assertTrue((self.out / "README.md").read_text().startswith("# Low water\n"))
        # The cut keeps the title it was saved with.
        self.assertEqual(timelines.load(NAME).title, "The quay")
        self.assertNotIn("Also in this folder", (self.out / "README.md").read_text())

    def test_a_preview_is_rendered_small_beside_the_files(self):
        status, out, err = test_cli.run("export", NAME, "--to", str(self.out), "--preview")
        self.assertEqual((status, err), (0, ""))
        preview = self.out / f"{NAME}-preview.mp4"
        lines = out.splitlines()
        self.assertEqual(lines[0], f"{NAME}: 2 clips, 0:01.6, at 25 frames a second, from 3 recordings")
        self.assertRegex(lines[1], r"^  a preview to watch, 960x540, \d+\.\d MB$")
        self.assertEqual(lines[2:], [str(self.out / f"{NAME}.fcpxml"), str(self.out / f"{NAME}.edl"),
                                     str(self.out / "README.md"), str(preview)])
        seen = ffmpeg.probe(preview)
        self.assertEqual((seen.width, seen.height, seen.has_audio), (960, 540, True))
        self.assertAlmostEqual(seen.duration_s, 1.6, delta=0.15)
        page = (self.out / "README.md").read_text()
        self.assertIn(f"## Also in this folder\n\n- `{NAME}-preview.mp4` is the cut to watch, small: 960x540.\n", page)
        self.assertIn("These are for watching. The two files above do not use them.", page)
        self.assertLess(page.index("## Also in this folder"), page.index("## The clips"))
        # It is on record as made from the cut's recordings, and marked as a preview.
        known = derive.lineage(preview)
        self.assertEqual((known.kind, known.params["timeline"], known.params["preview"]), ("render", NAME, True))
        self.assertEqual(sorted(known.sources), ["lapel.wav", "plain.mp4", "quay cam.mov"])
        # So it is never taken for a render of the cut.
        self.assertEqual(handover.renders(NAME), [])
        status, _, err = test_cli.run("export", NAME, "--to", str(self.out), "--preview", "--renders")
        self.assertEqual((status, err),
                         (1, f"media: no render of it is on record; `media render {NAME}` makes one\n"))

    def test_a_preview_is_kept_to_the_megabytes_it_is_allowed(self):
        free = handover.preview(NAME, self.out / "free", settings=self.settings)
        held = handover.preview(NAME, self.out / "held", megabytes=0.05, settings=self.settings)
        self.assertEqual((held.width, held.height), (free.width, free.height))
        self.assertLess(held.path.stat().st_size, free.path.stat().st_size)
        # The size is a limit: a cut that is under it anyway is not made bigger to reach it.
        roomy = handover.preview(NAME, self.out / "roomy", megabytes=500, settings=self.settings)
        self.assertAlmostEqual(roomy.path.stat().st_size, free.path.stat().st_size,
                               delta=free.path.stat().st_size * 0.1)
        with self.assertRaisesRegex(EditError, "a preview has to be allowed some megabytes"):
            handover.preview(NAME, self.out / "none", megabytes=0, settings=self.settings)
        self.assertFalse((self.out / "none").exists())

    def test_megabytes_are_only_about_a_preview(self):
        status, out, err = test_cli.run("export", NAME, "--to", str(self.out), "--megabytes", "5")
        self.assertEqual((status, out), (2, ""))
        self.assertEqual(err, "media: --megabytes says how big the preview may be; give --preview with it\n")
        self.assertFalse(self.out.exists())

    def test_the_renders_made_of_a_cut_are_copied_in_and_recorded(self):
        wide = self.render(self.made / "wide.mp4")
        status, out, err = test_cli.run("export", NAME, "--to", str(self.out), "--renders")
        self.assertEqual((status, err), (0, ""))
        copy = self.out / "wide.mp4"
        self.assertEqual(out.splitlines()[1:], [
            "  1 render of it beside them",
            str(self.out / f"{NAME}.fcpxml"), str(self.out / f"{NAME}.edl"), str(self.out / "README.md"), str(copy),
        ])
        # Copied and not moved.
        self.assertTrue(wide.is_file())
        self.assertEqual(copy.read_bytes(), wide.read_bytes())
        self.assertIn("## Also in this folder\n\n- `wide.mp4` is a render of the cut.\n",
                      (self.out / "README.md").read_text())
        known = derive.lineage(copy)
        self.assertEqual((known.kind, known.tool, known.params, known.sources),
                         ("export", "media export", {"timeline": NAME, "copy_of": str(wide)}, ("wide.mp4",)))
        self.assertEqual(self.query(
            """SELECT s.role FROM media.derivation_sources s JOIN media.derivations d ON d.id = s.derivation_id
                JOIN media.assets a ON a.id = d.output_id WHERE a.path = %s""", (str(copy),)), [("copied",)])
        # A copy is not a render of the cut, so asking again copies the one render again and no more.
        status, again, _ = test_cli.run("export", NAME, "--to", str(self.out), "--renders")
        self.assertEqual((status, again), (0, out))
        self.assertEqual(sorted(file.name for file in self.out.iterdir()),
                         ["README.md", f"{NAME}.edl", f"{NAME}.fcpxml", "wide.mp4"])

    def test_renders_of_one_name_are_told_apart_and_one_already_there_is_left(self):
        first = self.render(self.made / "one" / "cut.mp4")
        second = self.render(self.made / "two" / "cut.mp4")
        elsewhere = self.render(self.made / "square.mp4")
        here = self.render(self.out / "square.mp4")
        stranger = self.out / "cut-2.mp4"
        stranger.write_bytes(b"somebody else's file")
        self.age(first)
        copied, gone = handover.gather(NAME, self.out)
        self.assertEqual(gone, [])
        self.assertEqual(sorted((copy.file.name, copy.of, copy.stale) for copy in copied), [
            ("cut-3.mp4", second, False),
            ("cut.mp4", first, True),
            ("square-2.mp4", elsewhere, False),
            ("square.mp4", here, False),
        ])
        self.assertEqual(copied[0].of, first)
        # Nothing that was there was written over: not the render made here, and not a file nobody recorded.
        self.assertEqual(stranger.read_bytes(), b"somebody else's file")
        self.assertNotEqual((self.out / "square-2.mp4").stat().st_ino, here.stat().st_ino)
        # The one written here in the first place is still a render, and was not written over.
        self.assertEqual(derive.lineage(here).kind, "render")
        status, out, err = test_cli.run("export", NAME, "--to", str(self.out), "--renders")
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(out.splitlines()[1], "  4 renders of it beside them, 1 made before the cut was last saved")
        page = (self.out / "README.md").read_text()
        self.assertIn("- `cut.mp4` is a render of the cut as it was before it was last saved.\n", page)
        for name in ("cut-3.mp4", "square-2.mp4", "square.mp4"):
            self.assertIn(f"- `{name}` is a render of the cut.\n", page)
        # Asked again, each copy is written over itself and no more are made.
        self.assertEqual(sorted(file.name for file in self.out.glob("*.mp4")),
                         ["cut-2.mp4", "cut-3.mp4", "cut.mp4", "square-2.mp4", "square.mp4"])

    def test_a_render_that_is_gone_is_named_and_the_rest_still_handed_over(self):
        kept = self.render(self.made / "kept.mp4")
        lost = self.render(self.made / "lost.mp4")
        lost.unlink()
        status, out, err = test_cli.run("export", NAME, "--to", str(self.out), "--renders")
        self.assertEqual((status, err), (1, "media: 1 render of it on record is no longer there: lost.mp4\n"))
        self.assertEqual(out.splitlines()[1], "  1 render of it beside them")
        self.assertEqual((self.out / "kept.mp4").read_bytes(), kept.read_bytes())
        self.assertTrue((self.out / f"{NAME}.fcpxml").is_file())

    def test_a_cut_never_rendered_has_none_to_hand_over(self):
        status, out, err = test_cli.run("export", NAME, "--to", str(self.out), "--renders")
        self.assertEqual((status, err),
                         (1, f"media: no render of it is on record; `media render {NAME}` makes one\n"))
        # What could be written was.
        self.assertEqual(sorted(file.name for file in self.out.iterdir()),
                         ["README.md", f"{NAME}.edl", f"{NAME}.fcpxml"])
        # A render of another cut is not one of this cut.
        timelines.save("made-up-other", [Clip(self.assets["plain"], 0.0, 0.8)])
        render.render("made-up-other", self.made / "other.mp4", settings=self.settings)
        self.assertEqual(handover.renders(NAME), [])

    def test_everything_at_once_is_one_folder_to_send(self):
        self.render(self.made / "wide.mp4")
        handed = handover.hand_over(NAME, self.out, title="Low water", with_preview=True, with_renders=True,
                                    settings=self.settings)
        self.assertEqual(sorted(file.name for file in self.out.iterdir()),
                         ["README.md", f"{NAME}-preview.mp4", f"{NAME}.edl", f"{NAME}.fcpxml", "wide.mp4"])
        self.assertEqual([(file.name, what) for file, what in handed.exported.beside],
                         [(f"{NAME}-preview.mp4", "the cut to watch, small: 960x540"),
                          ("wide.mp4", "a render of the cut")])
        self.assertEqual((handed.preview.path, [copy.file for copy in handed.copied], handed.gone),
                         (self.out / f"{NAME}-preview.mp4", [self.out / "wide.mp4"], ()))
