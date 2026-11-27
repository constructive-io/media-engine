"""The skill and the examples say what the engine does.

Every `me` command the skill names is a real one, the script it shows is a
script the reader accepts, and every file it points at is there. The example
speech server answers the request the `speech` slot sends.
"""

import argparse
import importlib.util
import io
import json
import math
import re
import shutil
import struct
import threading
import unittest
import urllib.error
import urllib.request
import wave
from pathlib import Path

from constructive_media import cli, conv

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / ".agents" / "skills" / "constructive-media"
PAGES = [SKILL / "SKILL.md", *sorted((SKILL / "references").glob("*.md"))]

BLOCK = re.compile(r"^```(\w*)\n(.*?)^```", re.MULTILINE | re.DOTALL)
LINK = re.compile(r"\]\((?!https?:|#)([^)#]+)(?:#[^)]*)?\)")


def blocks(page: Path, kind: str) -> list[str]:
    return [body for found, body in BLOCK.findall(page.read_text(encoding="utf-8")) if found == kind]


def commands() -> set[str]:
    for action in cli.parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    return set()


class TheSkill(unittest.TestCase):
    def test_it_says_what_it_is_for(self):
        head = (SKILL / "SKILL.md").read_text(encoding="utf-8").split("---")[1]
        self.assertIn("name: constructive-media\n", head)
        self.assertRegex(head, r"description: .{80,}")

    def test_every_command_it_names_is_one(self):
        known, named = commands(), set()
        for page in PAGES:
            for block in blocks(page, "sh"):
                named |= set(re.findall(r"^me ([a-z-]+)", block, re.MULTILINE))
        self.assertGreater(len(named), 30)
        self.assertEqual(named - known, set())

    def test_the_script_it_shows_is_a_script(self):
        (shown,) = blocks(SKILL / "SKILL.md", "conv")
        scene = conv.parse(shown)
        self.assertEqual(sorted(scene["characters"]), ["ADA", "GRACE"])
        self.assertEqual(sum(item["type"] == "line" for item in scene["items"]), 3)

    def test_everything_it_points_at_is_there(self):
        for page in PAGES:
            for target in LINK.findall(page.read_text(encoding="utf-8")):
                self.assertTrue((page.parent / target).exists(), f"{page.name} points at {target}")

    def test_it_points_only_at_repositories_that_are_there(self):
        # A renamed repository leaves old links behind unless something looks.
        ours = {"constructive", "constructive-media", "conversation-format", "media-engine-plugins",
                "voice-boilerplates", "video-boilerplates", "document-boilerplates", "media-boilerplates"}
        pages = [*PAGES, ROOT / "README.md", ROOT / "AGENTS.md", *sorted((ROOT / "docs").glob("*.md"))]
        for page in pages:
            named = set(re.findall(r"github(?:usercontent)?\.com/constructive-io/([\w.-]+)", page.read_text(encoding="utf-8")))
            self.assertEqual(named - ours, set(), f"{page.name} points at a repository this one does not know")
        self.assertTrue((ROOT / "tests" / "fixtures" / "conversation-format" / "README.md").is_file())

    def test_what_it_says_a_render_reports_is_what_one_reports(self):
        page = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        said = (ROOT / "python" / "constructive_media" / "cli" / "speech.py").read_text(encoding="utf-8")
        made = (ROOT / "python" / "constructive_media" / "audio" / "scene.py").read_text(encoding="utf-8")
        for words in ("need a listen:", "left out, having no file:", "seconds:"):
            self.assertTrue(words in page, f"the skill does not show `{words}`")
            self.assertTrue(words in said, f"`me say` no longer prints `{words}`")
        for key in ("needs_review", "left_out", "status"):
            self.assertTrue(f"`{key}`" in page or f".{key}`" in page, f"the skill does not name `{key}`")
            self.assertTrue(f'"{key}"' in made + said, f"a render no longer reports `{key}`")

    def test_the_agent_guide_and_the_readme_lead_to_it(self):
        for name in ("AGENTS.md", "README.md"):
            leads = ".agents/skills/constructive-media" in (ROOT / name).read_text(encoding="utf-8")
            self.assertTrue(leads, f"{name} does not mention the skill")


class TheExamples(unittest.TestCase):
    def test_the_scene_is_the_one_the_skill_shows(self):
        def said(scene):
            return [(item["type"], item.get("speaker"), item.get("text"), item.get("overrides"), item.get("seconds"))
                    for item in scene["items"]]

        (shown,) = blocks(SKILL / "SKILL.md", "conv")
        there = conv.load(ROOT / "examples" / "scene.conv")
        self.assertEqual(said(there), said(conv.parse(shown)))
        self.assertEqual(there["characters"], conv.parse(shown)["characters"])


def a_tone(text: str, voice: str) -> bytes:
    """A second of sound, where there is no voice to ask."""
    held = io.BytesIO()
    with wave.open(held, "wb") as sound:
        sound.setnchannels(1)
        sound.setsampwidth(2)
        sound.setframerate(24000)
        sound.writeframes(b"".join(
            struct.pack("<h", int(8000 * math.sin(2 * math.pi * 220 * n / 24000))) for n in range(24000)
        ))
    return held.getvalue()


class TheMacVoiceServer(unittest.TestCase):
    """The example answers as a speech server should.

    On a Mac the words are really said. Anywhere else there is no `say`
    program, so a tone stands in for the voice and the answering is what is
    tested.
    """

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("mac_voice_server", ROOT / "examples" / "mac-voice-server.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not (shutil.which("say") and shutil.which("ffmpeg")):
            module.speak = a_tone
        cls.server = module.serve(0)
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}/v1"

    def ask(self, path: str, body: dict):
        request = urllib.request.Request(
            self.url + path, json.dumps(body).encode(), {"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as answer:
                return answer.status, answer.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def test_it_says_the_words_as_a_wav_file(self):
        status, sound = self.ask("/audio/speech", {"model": "say", "input": "The lantern is lit.", "seed": 7})
        self.assertEqual(status, 200)
        self.assertEqual((sound[:4], sound[8:12]), (b"RIFF", b"WAVE"))
        self.assertGreater(len(sound), 10_000)

    def test_a_request_it_cannot_answer_says_why(self):
        self.assertEqual(self.ask("/audio/speech", {"model": "say"})[0], 400)
        status, why = self.ask("/elsewhere", {"input": "Hello."})
        self.assertEqual(status, 404)
        self.assertIn("error", json.loads(why))


if __name__ == "__main__":
    unittest.main()
