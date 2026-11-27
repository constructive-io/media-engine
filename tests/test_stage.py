"""The stage: a render as a timeline, the page served, and frames laid on the clock."""

import json
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest import mock
from pathlib import Path

from constructive_media import ffmpeg
from constructive_media.browser import Filmed, Frame
from constructive_media.stage import PAGE, StageError, film, serve, timeline

from .support import make_render, make_still, make_tone, make_video, needs_ffmpeg


def fetch(address, **headers):
    with urllib.request.urlopen(urllib.request.Request(address, headers=headers)) as answer:
        return answer.status, dict(answer.headers), answer.read()


class StandIn:
    """A browser that paints three pictures: one before the clock starts, two after."""

    name = "stand-in"

    def __init__(self, pictures, notes=()):
        self.pictures, self.notes, self.asked = pictures, list(notes), None

    def film(self, url, folder, *, size, seconds, tell=None):
        self.asked = {"url": url, "size": size, "seconds": seconds}
        with urllib.request.urlopen(url) as answer:   # the page is really being served
            self.page = answer.read().decode()
        times = (99.9, 100.5, 101.0, 100.0 + seconds + 1)   # the last is after the end, and left out
        return Filmed(100.0, [Frame(at, file) for at, file in zip(times, self.pictures)], self.notes)


@needs_ffmpeg
class TimelineTest(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.folder = Path(scratch.name)

    def test_a_manifest_becomes_a_folder_a_page_can_follow(self):
        manifest = make_render(self.folder / "out")
        made = timeline.build(manifest, self.folder / "stage")
        self.assertEqual(json.loads((self.folder / "stage" / "timeline.json").read_text()), made)
        self.assertEqual(made["version"], 1)
        self.assertEqual((made["label"], made["sound"], made["seconds"]), ("scene", "sound.wav", 3.0))
        self.assertTrue((self.folder / "stage" / "sound.wav").is_file())
        self.assertEqual(made["slides"], [{"t": 0.0, "id": "title", "step": 0}, {"t": 1.5, "id": "title", "step": 1}])
        self.assertEqual(made["speakers"], [{"t": 0.0, "show": ["faces", "captions"]}])
        self.assertEqual([line["persona"] for line in made["lines"]], ["ada", "grace"])
        self.assertEqual((made["lines"][0]["name"], made["lines"][0]["title"]), ("Ada Marsh", "Keeper"))
        self.assertNotIn("name", made["lines"][1])
        self.assertEqual(made["clips"], [])

    def test_the_sound_is_found_from_its_own_path(self):
        make_render(self.folder / "out")
        self.assertEqual(timeline.read(self.folder / "out" / "scene.wav")["label"], "scene")

    def test_every_line_has_its_loudness(self):
        made = timeline.build(make_render(self.folder / "out"), self.folder / "stage")
        self.assertEqual(made["loudness"], {"rate": 25, "top": 99})
        for line in made["lines"]:
            self.assertEqual(len(line["loudness"]), 25)        # a second, 25 times a second
            self.assertTrue(all(0 <= value <= 99 for value in line["loudness"]))
            self.assertGreater(max(line["loudness"]), 90)      # a steady tone is as loud as it gets

    def test_a_line_that_is_not_on_disk_is_said_and_has_no_loudness(self):
        manifest = make_render(self.folder / "out")
        (self.folder / "out" / "scene.lines" / "line_002.wav").unlink()
        told = []
        made = timeline.build(manifest, self.folder / "stage", tell=told.append)
        self.assertEqual(made["lines"][1]["loudness"], [])
        self.assertIn("line 2", told[0])

    def test_a_clip_is_cut_to_what_is_shown_at_the_film_s_size(self):
        clip = make_video(self.folder / "clip.mp4", seconds=1.0)
        made = timeline.build(make_render(self.folder / "out", clip=clip), self.folder / "stage",
                              size=(320, 180), fps=25)
        self.assertEqual(made["clips"], [{"t": 1.2, "seconds": 0.5, "src": "clips/clip_01.mp4", "sound": "DEMO"}])
        cut = ffmpeg.probe(self.folder / "stage" / "clips" / "clip_01.mp4")
        self.assertEqual((cut.width, cut.height), (320, 180))
        self.assertFalse(cut.has_audio)   # its sound is in the mix
        self.assertAlmostEqual(cut.duration_s, 0.5, delta=0.1)

    def test_a_clip_with_no_file_is_said_and_left_out(self):
        told = []
        made = timeline.build(make_render(self.folder / "out", clip=self.folder / "nowhere.mp4"),
                              self.folder / "stage", tell=told.append)
        self.assertEqual(made["clips"], [])
        self.assertIn("DEMO", told[0])

    def test_faces_are_the_pictures_named_for_who_speaks(self):
        make_still(self.folder / "faces" / "ada.png")
        make_still(self.folder / "faces" / "nobody.png")
        made = timeline.build(make_render(self.folder / "out"), self.folder / "stage", faces=self.folder / "faces")
        self.assertEqual(made["faces"], {"ada": "faces/ada.png"})
        self.assertTrue((self.folder / "stage" / "faces" / "ada.png").is_file())
        self.assertFalse((self.folder / "stage" / "faces" / "nobody.png").exists())

    def test_sound_a_browser_cannot_play_is_written_again(self):
        made = timeline.build(make_render(self.folder / "out", ending=".aiff"), self.folder / "stage")
        self.assertEqual(made["sound"], "sound.wav")
        self.assertTrue((self.folder / "stage" / "sound.wav").is_file())

    def test_what_is_not_a_manifest_is_refused(self):
        with self.assertRaisesRegex(StageError, "no such manifest"):
            timeline.read(self.folder / "nothing.flac")
        (self.folder / "other.json").write_text("{}")
        with self.assertRaisesRegex(StageError, "not the manifest of a render"):
            timeline.read(self.folder / "other.json")
        manifest = make_render(self.folder / "out")
        (self.folder / "out" / "scene.wav").unlink()
        with self.assertRaisesRegex(StageError, "is not there"):
            timeline.build(manifest, self.folder / "stage")


class ServeTest(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.folder = Path(scratch.name)
        (self.folder / "deck").mkdir()
        (self.folder / "deck" / "index.html").write_text("<body data-stage=deck>a deck</body>")
        (self.folder / "staged").mkdir()
        (self.folder / "staged" / "timeline.json").write_text('{"seconds": 1}')
        (self.folder / "staged" / "sound.wav").write_bytes(bytes(range(100)))
        (self.folder / "secret.txt").write_text("not served")

    def test_the_engine_ships_its_page(self):
        for name in ("index.html", "stage.js", "stage.css"):
            self.assertTrue((PAGE / name).is_file(), name)
        self.assertIn("window.__stage", (PAGE / "stage.js").read_text())

    def test_the_ways_a_thing_arrives_are_all_in_the_sheet_and_the_player_says_when(self):
        sheet, player = (PAGE / "stage.css").read_text(), (PAGE / "stage.js").read_text()
        for way in ("fade", "left", "right", "grow", "pop", "wipe", "draw"):
            self.assertIn(f'[data-enter="{way}"]', sheet, way)
        for held in ("var(--stage-delay, 0s)", "var(--stage-time, "):
            self.assertIn(held, sheet)
        # What is written in docs/STAGE.md for a page to use.
        for said in ("when: function (slide, step)", "since: function (slide, step)", "body.dataset.stageSpeaker",
                     "setAttribute('pathLength', '1')"):
            self.assertIn(said, player)
        written = (Path(__file__).resolve().parents[1] / "docs" / "STAGE.md").read_text()
        for way in ("rise", "fade", "left", "right", "grow", "pop", "wipe", "draw"):
            self.assertIn(f"| `{way}` |", written, way)
        for said in ("--stage-delay", "--stage-time", "stage.since(", "stage.when(", "data-stage-speaker"):
            self.assertIn(said, written)

    def test_what_is_written_on_a_panel_does_not_take_the_page_s_ink(self):
        # A page with dark ink on a light ground still has captions on a dark panel.
        sheet = (PAGE / "stage.css").read_text()
        overlay = sheet[sheet.index(".stage-overlay {"):]
        overlay = overlay[:overlay.index("}")]
        self.assertIn("color: var(--stage-panel-ink);", overlay)
        self.assertNotIn("var(--stage-ink)", overlay)
        self.assertIn("\n  --stage-panel-ink: #", sheet)

    def test_a_page_and_its_staged_folder_are_served(self):
        with serve.Stage(self.folder / "staged", page=self.folder / "deck") as stage:
            status, headers, body = fetch(stage.address + "/")
            self.assertEqual((status, body), (200, b"<body data-stage=deck>a deck</body>"))
            self.assertTrue(headers["Content-Type"].startswith("text/html"))
            status, headers, body = fetch(stage.address + "/_stage/timeline.json")
            self.assertEqual(json.loads(body), {"seconds": 1})
            self.assertEqual(headers["Access-Control-Allow-Origin"], "*")

    def test_the_player_comes_from_the_engine_when_the_page_has_none(self):
        with serve.Stage(page=self.folder / "deck") as stage:
            self.assertEqual(fetch(stage.address + "/stage.js")[2], (PAGE / "stage.js").read_bytes())
            self.assertEqual(fetch(stage.address + "/stage.css")[2], (PAGE / "stage.css").read_bytes())
        (self.folder / "deck" / "stage.css").write_text("/* the page's own */")
        with serve.Stage(page=self.folder / "deck") as stage:
            self.assertEqual(fetch(stage.address + "/stage.css")[2], b"/* the page's own */")

    def test_with_no_page_the_plain_one_is_served(self):
        with serve.Stage(self.folder / "staged") as stage:
            self.assertIn(b'data-stage="plain"', fetch(stage.address + "/")[2])

    def test_a_single_html_file_is_a_page(self):
        (self.folder / "deck" / "other.html").write_text("another")
        with serve.Stage(page=self.folder / "deck" / "other.html") as stage:
            self.assertEqual(fetch(stage.url())[2], b"another")
            self.assertTrue(stage.url().endswith("/other.html"))

    def test_part_of_a_file_can_be_asked_for(self):
        with serve.Stage(self.folder / "staged", page=self.folder / "deck") as stage:
            status, headers, body = fetch(stage.address + "/_stage/sound.wav", Range="bytes=10-19")
            self.assertEqual((status, body), (206, bytes(range(10, 20))))
            self.assertEqual(headers["Content-Range"], "bytes 10-19/100")
            status, _, body = fetch(stage.address + "/_stage/sound.wav", Range="bytes=90-")
            self.assertEqual((status, body), (206, bytes(range(90, 100))))
            with self.assertRaises(urllib.error.HTTPError) as refused:
                fetch(stage.address + "/_stage/sound.wav", Range="bytes=500-")
            self.assertEqual(refused.exception.code, 416)

    def test_nothing_outside_the_two_folders_is_served(self):
        with serve.Stage(self.folder / "staged", page=self.folder / "deck") as stage:
            for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/_stage/../secret.txt", "/missing.html"):
                with self.assertRaises(urllib.error.HTTPError) as refused:
                    fetch(stage.address + path)
                self.assertEqual(refused.exception.code, 404, path)

    def test_the_address_says_what_to_follow(self):
        with serve.Stage(self.folder / "staged", page=self.folder / "deck") as stage:
            self.assertEqual(stage.url(), f"{stage.address}/?timeline=/_stage/timeline.json")
            self.assertEqual(stage.url(capture=True, size=(1280, 720)),
                             f"{stage.address}/?timeline=/_stage/timeline.json&capture=1&size=1280x720")
            # A page served somewhere else is told where this server is.
            self.assertEqual(stage.url("http://localhost:3000/deck?theme=dark", capture=True),
                             f"http://localhost:3000/deck?theme=dark&timeline={stage.address}/_stage/timeline.json"
                             "&capture=1")
        with serve.Stage(page=self.folder / "deck") as stage:
            self.assertEqual(stage.url(), stage.address + "/")

    def test_a_folder_with_no_page_is_refused(self):
        with self.assertRaisesRegex(StageError, "index.html"):
            serve.Stage(page=self.folder / "staged")


@needs_ffmpeg
class FilmTest(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.folder = Path(scratch.name)
        self.pictures = [make_still(self.folder / "pictures" / f"{number}.jpg", "320x180") for number in range(4)]

    def test_a_render_is_filmed_as_long_as_its_sound(self):
        manifest = make_render(self.folder / "out")
        browser, told = StandIn(self.pictures), []
        report = film.film(manifest, self.folder / "out" / "scene.mp4", size=(320, 180), fps=25,
                           browser=browser, tell=told.append)
        made = ffmpeg.probe(self.folder / "out" / "scene.mp4")
        self.assertEqual((made.width, made.height, made.codec), (320, 180, "h264"))
        self.assertTrue(made.has_audio)
        self.assertAlmostEqual(made.duration_s, 3.0, delta=0.1)
        self.assertEqual((report["frames"], report["slides"], report["lines"], report["clips"]), (3, 2, 2, 0))
        self.assertEqual(report["page"], "built in")
        # The browser was sent to the plain page, told to run for the camera.
        self.assertIn("capture=1", browser.asked["url"])
        self.assertIn("size=320x180", browser.asked["url"])
        self.assertIn('data-stage="plain"', browser.page)
        self.assertEqual(browser.asked["seconds"], 3.0)
        # Nothing is left beside the film.
        self.assertEqual(sorted(path.name for path in (self.folder / "out").iterdir()),
                         ["scene.lines", "scene.manifest.json", "scene.mp4", "scene.wav"])

    def test_a_page_s_own_faces_are_used_and_what_was_made_can_be_kept(self):
        manifest = make_render(self.folder / "out")
        (self.folder / "deck").mkdir()
        (self.folder / "deck" / "index.html").write_text("<body data-stage=deck></body>")
        make_still(self.folder / "deck" / "faces" / "grace.png")
        film.film(manifest, self.folder / "scene.mp4", page=self.folder / "deck", size=(320, 180),
                  browser=StandIn(self.pictures), keep=self.folder / "kept")
        kept = json.loads((self.folder / "kept" / "stage" / "timeline.json").read_text())
        self.assertEqual(kept["faces"], {"grace": "faces/grace.png"})

    def test_a_clip_takes_the_screen_while_it_plays(self):
        clip = make_video(self.folder / "clip.mp4", colours=("red",), seconds=1.0)
        manifest = make_render(self.folder / "out", clip=clip)
        blank = self.folder / "pictures" / "black.jpg"
        ffmpeg.run("ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=black:s=320x180",
                   "-frames:v", "1", "-update", "1", blank)
        report = film.film(manifest, self.folder / "scene.mp4", size=(320, 180), fps=25,
                           browser=StandIn([blank] * 4))
        self.assertEqual(report["clips"], 1)

        def red(at):
            raw = ffmpeg.run("ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(at), "-i",
                             self.folder / "scene.mp4", "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo",
                             "-pix_fmt", "rgb24", "-", binary=True).stdout
            return raw[0] > 150 and raw[1] < 80

        self.assertFalse(red(0.6))    # before the clip, the page
        self.assertTrue(red(1.45))    # the clip, from 1.2 to 1.7
        self.assertFalse(red(2.4))    # and the page again

    def test_what_the_page_raised_is_told(self):
        told = []
        report = film.film(make_render(self.folder / "out"), self.folder / "scene.mp4", size=(320, 180),
                           browser=StandIn(self.pictures, notes=["ReferenceError: deck is not defined"]),
                           tell=told.append)
        self.assertEqual(report["notes"], ["ReferenceError: deck is not defined"])
        self.assertTrue(any("deck is not defined" in line for line in told))

    def test_a_browser_that_painted_nothing_is_an_error(self):
        with self.assertRaisesRegex(StageError, "painted nothing"):
            film.film(make_render(self.folder / "out"), self.folder / "scene.mp4", size=(320, 180),
                      browser=StandIn([]))

    def test_sizes_that_cannot_be_filmed_are_refused(self):
        manifest = make_render(self.folder / "out")
        with self.assertRaisesRegex(StageError, "even"):
            film.film(manifest, self.folder / "scene.mp4", size=(321, 180), browser=StandIn(self.pictures))
        with self.assertRaisesRegex(StageError, "not both"):
            film.film(manifest, self.folder / "scene.mp4", page=self.folder, url="http://localhost:3000/",
                      browser=StandIn(self.pictures))

    def test_a_bitrate_is_read_as_it_is_written(self):
        self.assertEqual([timeline.bits(written) for written in ("2M", "800k", " 1.5m ", "250000", 64000)],
                         [2_000_000, 800_000, 1_500_000, 250_000, 64_000])
        for written in ("fast", "", "M", "-2M", "10k", "inf", "nan"):
            with self.subTest(written=written), self.assertRaisesRegex(StageError, "bits a second"):
                timeline.bits(written)

    def test_a_film_is_written_at_the_bitrate_asked_for(self):
        manifest = make_render(self.folder / "out")
        with self.assertRaisesRegex(StageError, "bits a second"):      # said before anything is filmed
            film.film(manifest, self.folder / "scene.mp4", size=(320, 180), bitrate="fast", browser=StandIn([]))
        asked = []
        encode = ffmpeg.encode

        def watched(*arguments, **more):
            asked.append([str(one) for one in arguments])
            return encode(*arguments, **more)

        with mock.patch.object(ffmpeg, "encode", watched):
            report = film.film(manifest, self.folder / "scene.mp4", size=(320, 180), fps=25, bitrate="300k",
                               browser=StandIn(self.pictures))
            usual = film.film(manifest, self.folder / "usual.mp4", size=(320, 180), fps=25,
                              browser=StandIn(self.pictures))
        self.assertEqual([one[one.index("-b:v") + 1] for one in asked], ["300000", "1000000"])
        self.assertEqual((report["bitrate"], usual["bitrate"]), (300_000, 1_000_000))
        self.assertEqual(report["bytes"], (self.folder / "scene.mp4").stat().st_size)

    def test_the_first_picture_is_the_one_painted_as_the_clock_began(self):
        made = {"seconds": 2.0, "sound": "sound.wav", "clips": []}
        make_tone(self.folder / "sound.wav", 2.0)
        frames = [Frame(9.0, self.pictures[0]), Frame(9.9, self.pictures[1]), Frame(10.5, self.pictures[2]),
                  Frame(12.0, self.pictures[3])]
        _, shown = film.assemble(Filmed(10.0, frames, []), made, self.folder, self.folder / "out.mp4",
                                 size=(320, 180), fps=25)
        self.assertEqual(shown, 2)   # the one at 9.9, held from 0, and the one at 10.5; 12.0 is at the end
        self.assertEqual(list(self.folder.glob(".*.frames.txt")), [])


if __name__ == "__main__":
    unittest.main()
