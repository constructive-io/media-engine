#!/usr/bin/env python3
"""A speech server to try the engine with, on a Mac: the system's own voice.

It answers the `audio/speech` request the engine's `speech` slot sends, by
running the `say` program that comes with macOS. It is here so that a script
can be heard before any speech model is set up. It is not one: it has a few
plain voices and it reads every seed the same way.

    python3 examples/mac-voice-server.py            # listens on 127.0.0.1:8080

    export CONSTRUCTIVE_MEDIA_URL_SPEECH=http://127.0.0.1:8080/v1
    export CONSTRUCTIVE_MEDIA_MODEL_SPEECH=say
    media voice Ada Samantha --new                  # `say -v '?'` lists the voices

Needs `say` (macOS) and `ffmpeg`. Standard library only.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFAULT_VOICE = "Samantha"
RATE = 24000


def speak(text: str, voice: str) -> bytes:
    """The words said by one of the system's voices, as a WAV file."""
    with tempfile.TemporaryDirectory() as here:
        said, wav = Path(here, "said.aiff"), Path(here, "said.wav")
        subprocess.run(["say", "-v", voice, "-o", str(said), "--", text],
                       check=True, capture_output=True)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(said), "-ar", str(RATE), str(wav)],
                       check=True, capture_output=True)
        return wav.read_bytes()


class Speech(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        if not self.path.rstrip("/").endswith("/audio/speech"):
            return self.answer(404, {"error": f"nothing answers at {self.path}"})
        try:
            asked = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            text = str(asked["input"])
        except (ValueError, KeyError, TypeError):
            return self.answer(400, {"error": "send JSON with the words to say as `input`"})
        voice = str(asked.get("voice") or DEFAULT_VOICE)
        try:
            sound = speak(text, voice)
        except FileNotFoundError as error:
            return self.answer(500, {"error": f"{error.filename} is not installed"})
        except subprocess.CalledProcessError as error:
            why = (error.stderr or b"").decode(errors="replace").strip() or "the words could not be said"
            return self.answer(500, {"error": why})
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(sound)))
        self.end_headers()
        self.wfile.write(sound)

    def answer(self, status: int, body: dict) -> None:
        sent = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(sent)))
        self.end_headers()
        self.wfile.write(sent)

    def log_message(self, *args: object) -> None:  # one quiet line a request is plenty
        pass


def serve(port: int = 8080, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), Speech)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    server = serve(port)
    print(f"speaking at http://127.0.0.1:{server.server_address[1]}/v1  (ctrl-c to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
