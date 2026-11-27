"""The browser slot: Chrome driven over a pipe, against a stand-in program and against the real one."""

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from constructive_media import adapters, browser, ffmpeg
from constructive_media.browser import BrowserError, chrome
from constructive_media.stage import film, serve

from .support import HAVE_FFMPEG, make_render, settings_in

# A program that answers as a browser does: messages end with a zero byte, in on 3 and out on 4.
STAND_IN = r'''
import base64, json, os, sys
log = open(os.environ["STAND_IN_LOG"], "a")
mode = os.environ.get("STAND_IN_MODE", "films")
print(json.dumps({"argv": sys.argv[1:]}), file=log, flush=True)
asked, held = 0, b""

def say(message):
    os.write(4, json.dumps(message).encode() + b"\0")

while True:
    piece = os.read(3, 65536)
    if not piece:
        break
    held += piece
    while b"\0" in held:
        raw, held = held.split(b"\0", 1)
        message = json.loads(raw)
        method, session = message["method"], message.get("sessionId")
        print(json.dumps(message), file=log, flush=True)
        result = {}
        if method == "Browser.close":
            say({"id": message["id"], "result": {}})
            sys.exit(0)
        if method == "Target.createTarget":
            result = {"targetId": "a-tab"}
        elif method == "Target.attachToTarget":
            result = {"sessionId": "a-session"}
        elif method == "Page.navigate" and mode == "dies":
            sys.exit(1)
        elif method == "Page.navigate" and mode == "refuses":
            say({"id": message["id"], "sessionId": session, "error": {"message": "Cannot navigate to invalid URL"}})
            continue
        elif method == "Page.printToPDF":
            result = {"data": base64.b64encode(b"%PDF-1.4 a stand-in").decode()}
        elif method == "Runtime.evaluate" and "__stage" in message["params"]["expression"]:
            asked += 1
            state = None
            if mode == "films":
                state = {"startEpoch": 1000000, "seconds": 0.5, "done": asked >= 4}
                say({"method": "Page.screencastFrame", "sessionId": session, "params": {
                    "data": base64.b64encode(b"picture %d" % asked).decode(), "sessionId": asked,
                    "metadata": {"timestamp": 1000.0 + asked / 10}}})
                if asked == 2:
                    say({"method": "Runtime.exceptionThrown", "sessionId": session, "params": {"exceptionDetails": {
                        "text": "Uncaught", "exception": {"description": "TypeError: no deck\n    at page.js:1"}}}})
            elif mode == "fails":
                state = {"error": "the timeline could not be read: 404"}
            result = {"result": {"type": "string", "value": json.dumps(state)}}
        say({"id": message["id"], "sessionId": session, "result": result})
'''


class StandInTest(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.folder = Path(scratch.name)
        self.program = self.folder / "browser"
        self.program.write_text(f"#!{sys.executable}\n{STAND_IN}")
        self.program.chmod(self.program.stat().st_mode | stat.S_IXUSR)
        self.log = self.folder / "log"
        self.settings = settings_in(self.folder / "home", models={"browser": str(self.program)})

    def use(self, mode="films"):
        patched = mock.patch.dict(os.environ, {"STAND_IN_LOG": str(self.log), "STAND_IN_MODE": mode})
        patched.start()
        self.addCleanup(patched.stop)
        return chrome.Chrome(settings=self.settings)

    def sent(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_the_slot_is_filled_by_chrome(self):
        self.assertIn("browser", adapters.SLOTS)
        self.assertEqual(adapters.registry().describe("browser", settings=self.settings).name, "chrome")
        self.assertTrue(callable(browser.browser))

    def test_a_page_is_filmed_until_it_says_it_is_done(self):
        filmed = self.use().film("http://127.0.0.1:1/?capture=1", self.folder / "frames", size=(640, 360),
                                 seconds=0.5)
        self.assertEqual(filmed.start, 1000.0)
        self.assertEqual([frame.at for frame in filmed.frames], [1000.1, 1000.2, 1000.3, 1000.4])
        self.assertEqual([frame.file.read_bytes() for frame in filmed.frames],
                         [b"picture %d" % number for number in (1, 2, 3, 4)])
        self.assertEqual(filmed.notes, ["TypeError: no deck"])
        sent = self.sent()
        self.assertIn("--remote-debugging-pipe", sent[0]["argv"])
        self.assertIn("--window-size=640,360", sent[0]["argv"])
        methods = [message["method"] for message in sent[1:]]
        self.assertLess(methods.index("Page.startScreencast"), methods.index("Page.navigate"))
        self.assertEqual(methods.count("Page.screencastFrameAck"), 4)    # every picture is answered for
        self.assertEqual(methods[-1], "Browser.close")
        metrics = [message for message in sent[1:] if message["method"] == "Emulation.setDeviceMetricsOverride"][0]
        self.assertEqual((metrics["params"]["width"], metrics["params"]["height"]), (640, 360))
        self.assertEqual(metrics["sessionId"], "a-session")

    def test_a_page_is_printed(self):
        printed = self.use().print("http://127.0.0.1:1/", self.folder / "out" / "page.pdf")
        self.assertEqual(printed.read_bytes(), b"%PDF-1.4 a stand-in")

    def test_a_page_that_never_starts_is_an_error(self):
        with mock.patch.object(chrome, "READY_S", 0.4):
            with self.assertRaisesRegex(BrowserError, "never said it had started"):
                self.use("silent").film("http://127.0.0.1:1/", self.folder / "frames", size=(640, 360), seconds=1)
        self.assertEqual(self.sent()[-1]["method"], "Browser.close")   # and the browser is not left running

    def test_a_page_that_could_not_start_says_why(self):
        with self.assertRaisesRegex(BrowserError, "could not be read: 404"):
            self.use("fails").film("http://127.0.0.1:1/", self.folder / "frames", size=(640, 360), seconds=1)

    def test_a_browser_that_stops_is_an_error(self):
        with self.assertRaisesRegex(BrowserError, "stopped before it had finished"):
            self.use("dies").film("http://127.0.0.1:1/", self.folder / "frames", size=(640, 360), seconds=1)

    def test_what_the_browser_refuses_is_said(self):
        with self.assertRaisesRegex(BrowserError, "Cannot navigate"):
            self.use("refuses").print("nowhere", self.folder / "page.pdf")

    def test_more_flags_can_be_given(self):
        self.settings = settings_in(self.folder / "home", models={"browser": str(self.program)},
                                    extras={"browser": '{"flags": ["--no-sandbox"]}'})
        self.use().print("http://127.0.0.1:1/", self.folder / "page.pdf")
        self.assertIn("--no-sandbox", self.sent()[0]["argv"])
        with self.assertRaisesRegex(BrowserError, "EXTRA_BROWSER"):
            chrome.Chrome(settings=settings_in(self.folder / "home", models={"browser": str(self.program)},
                                               extras={"browser": '["--no-sandbox"]'}))

    def test_a_browser_that_is_not_there_is_said(self):
        with self.assertRaisesRegex(BrowserError, "not a program that can be run"):
            chrome.Chrome(settings=settings_in(self.folder / "home", models={"browser": "/nowhere/chrome"}))
        with mock.patch.object(chrome, "CANDIDATES", ("no-such-browser-anywhere",)):
            with self.assertRaisesRegex(BrowserError, "MODEL_BROWSER"):
                chrome.Chrome(settings=settings_in(self.folder / "home"))


def real_browser():
    try:
        return chrome.Chrome().program if HAVE_FFMPEG else None
    except BrowserError:
        return None


DECK = """<!doctype html><meta charset="utf-8">
<link rel="stylesheet" href="stage.css"><script src="stage.js" defer></script>
<style>
  [data-slide] { background: rgb(200, 0, 0); transition: none !important; }
  [data-step] { position: absolute; inset: 0; background: rgb(0, 160, 0); transition: none !important; }
</style>
<body data-stage="deck" data-stage-overlay="off"><section data-slide="title"><div data-step="1"></div></section></body>
"""


@unittest.skipUnless(real_browser(), "no Chrome or Chromium, or no ffmpeg, is installed")
class RealBrowserTest(unittest.TestCase):
    """The real thing, where there is one: a deck filmed, and its build seen in the film."""

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.folder = Path(scratch.name)
        (self.folder / "deck").mkdir()
        (self.folder / "deck" / "index.html").write_text(DECK)

    def colour(self, at):
        return tuple(ffmpeg.run("ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(at), "-i",
                                self.folder / "scene.mp4", "-frames:v", "1", "-vf", "crop=64:64:128:58,scale=1:1",
                                "-f", "rawvideo", "-pix_fmt", "rgb24", "-", binary=True).stdout[:3])

    def test_a_deck_is_filmed_with_its_build_where_the_script_put_it(self):
        report = film.film(make_render(self.folder / "out"), self.folder / "scene.mp4", page=self.folder / "deck",
                           size=(320, 180), fps=25)
        self.assertEqual(report["notes"], [])
        made = ffmpeg.probe(self.folder / "scene.mp4")
        self.assertEqual((made.width, made.height), (320, 180))
        self.assertAlmostEqual(made.duration_s, 3.0, delta=0.1)
        red, green, _ = self.colour(0.7)     # the slide, before `[slide title step:1]` at 1.5 seconds
        self.assertGreater(red, 140)
        self.assertLess(green, 60)
        red, green, _ = self.colour(2.5)     # and after it, the build covers the slide
        self.assertLess(red, 60)
        self.assertGreater(green, 100)

    def test_a_page_is_printed(self):
        with serve.Stage(page=self.folder / "deck") as stage:
            printed = chrome.Chrome().print(stage.url(), self.folder / "deck.pdf")
        self.assertEqual(printed.read_bytes()[:5], b"%PDF-")


if __name__ == "__main__":
    unittest.main()
