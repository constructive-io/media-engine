"""A conversation, served to a browser on this machine.

    with serve.Talk(parts, ears=hearing.ears(settings)) as served:
        print(served.address)

The page at that address opens one WebSocket, at `/talk`, and the whole
conversation goes over it. Up it go the microphone's sound, as binary
messages of 16-bit samples, one channel, 16,000 a second, and what is typed:

    {"type": "text", "text": "What is the tide doing?"}
    {"type": "hush"}                                      stop answering

Down it come the things `talk.Conversation` tells, each sentence's sound as
a binary message holding a WAV file, after one message that says what is on
the other end:

    {"type": "ready", "hears": true, ...}

It listens on this machine only, and it answers only a page it served
itself: a microphone and a model are not for any page that happens to be
open in the same browser.
"""

from __future__ import annotations

import json
import socket as sockets
import sys
import threading
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import Conversation, Parts, TalkError
from .hearing import Ears, Utterances
from .websocket import Closed, Socket, accepting

PAGE = Path(__file__).resolve().parent / "page"
FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/microphone.js": ("microphone.js", "text/javascript"),
}
WHERE = "/talk"


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        # A page that was closed, or a browser that gave up on a connection, is not a fault to print.
        if not isinstance(sys.exc_info()[1], OSError):
            super().handle_error(request, client_address)


class Talk:
    """A server for conversations with one set of models.

    `ears` makes what tells speech from a room, one for each microphone that
    is opened. Without it, or without a transcriber, turns can only be typed.
    `about` is anything worth telling the page about what it is talking to.
    """

    def __init__(self, parts: Parts, *, ears: Callable[[], Ears] | None = None, port: int = 0,
                 about: dict[str, Any] | None = None) -> None:
        self.parts = parts
        self.ears = ears if parts.transcriber is not None else None
        self.about = dict(about or {})
        self._open: set[sockets.socket] = set()
        self._lock = threading.Lock()
        handler = type("Handler", (_Handler,), {"talk": self})
        try:
            self._server = _Server(("127.0.0.1", port), handler)
        except OSError as error:
            raise TalkError(f"port {port} cannot be listened on: {error.strerror or error}") from None
        self._thread = threading.Thread(target=self._server.serve_forever, args=(0.05,), daemon=True)

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    @property
    def address(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def socket(self) -> str:
        """Where a conversation is opened, for something that is not the page."""
        return f"ws://127.0.0.1:{self.port}{WHERE}"

    def start(self) -> Talk:
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._thread.is_alive():
            self._server.shutdown()
        self._server.server_close()
        with self._lock:
            held, self._open = list(self._open), set()
        for connection in held:
            # Whoever is waiting on a conversation is let go.
            try:
                connection.shutdown(sockets.SHUT_RDWR)
            except OSError:
                pass

    def __enter__(self) -> Talk:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def ours(self, host: str | None, origin: str | None) -> bool:
        """Whether a request was made of this server by a page it served, or by no page at all.

        A page anywhere can ask a browser to reach this machine, and a name
        can be made to point here. Neither is this server's own page.
        """
        here = {f"{name}:{self.port}" for name in ("127.0.0.1", "localhost")}
        return host in here and (origin is None or origin in {f"http://{one}" for one in here})

    def converse(self, line: Socket) -> None:
        """Have one conversation over `line`, for as long as it stays open."""

        def tell(told: dict[str, Any] | bytes) -> None:
            try:
                line.send(told if isinstance(told, bytes) else json.dumps(told))
            except Closed:
                pass

        conversation = Conversation(self.parts, tell)
        said = Utterances(self.ears()) if self.ears is not None else None
        tell({"type": "ready", **self.about, "hears": said is not None})
        tell({"type": "state", "value": "listening"})
        try:
            while True:
                message = line.receive()
                if isinstance(message, bytes):
                    for heard in said.push(message) if said is not None else ():
                        if heard is None:
                            conversation.hush()
                        else:
                            conversation.heard(heard)
                    continue
                try:
                    asked = json.loads(message)
                except ValueError:
                    continue
                kind = asked.get("type") if isinstance(asked, dict) else None
                if kind == "text":
                    conversation.typed(str(asked.get("text") or ""))
                elif kind == "hush":
                    conversation.hush()
        except Closed:
            pass
        finally:
            conversation.close()
            line.close()


class _Handler(BaseHTTPRequestHandler):
    talk: Talk
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - the name the standard library calls
        self._answer(body=True)

    def do_HEAD(self) -> None:  # noqa: N802
        self._answer(body=False)

    def _answer(self, *, body: bool) -> None:
        if not self.talk.ours(self.headers.get("Host"), self.headers.get("Origin")):
            self.send_error(HTTPStatus.FORBIDDEN, "This serves the machine it runs on, and only its own page")
            return
        path = urlsplit(self.path).path
        if path == WHERE and body:
            self._converse()
            return
        if path not in FILES:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        name, kind = FILES[path]
        held = (PAGE / name).read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(held)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body:
            self.wfile.write(held)

    def _converse(self) -> None:
        key = self.headers.get("Sec-WebSocket-Key")
        if (self.headers.get("Upgrade") or "").lower() != "websocket" or not key:
            self.send_error(HTTPStatus.BAD_REQUEST, "A conversation is opened as a WebSocket")
            return
        if self.headers.get("Sec-WebSocket-Version") != "13":
            self.send_response(HTTPStatus.UPGRADE_REQUIRED)
            self.send_header("Sec-WebSocket-Version", "13")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accepting(key))
        self.end_headers()
        self.close_connection = True
        with self.talk._lock:
            self.talk._open.add(self.connection)
        try:
            self.talk.converse(Socket(self.rfile, self.wfile))
        finally:
            with self.talk._lock:
                self.talk._open.discard(self.connection)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - the name the standard library uses
        pass
