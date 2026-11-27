"""A page, its timeline and its sound, served to a browser on this machine.

    with serve.Stage(page="deck", staged="out/scene.stage") as stage:
        print(stage.url())

Two folders are served as one site. The page's own folder is the site; the
staged folder, the one `timeline.build` wrote, is under `/_stage/`. A page
that asks for `stage.js` or `stage.css` and has none of its own is given the
engine's, so a deck never has to carry a copy.

It listens on this machine only. Sound is served in parts when a part is
asked for, which is what lets a browser jump about in it.
"""

from __future__ import annotations

import mimetypes
import os
import re
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlencode, urlsplit

from . import MOUNT, PAGE, StageError

SHARED = ("stage.js", "stage.css")
_KINDS = {
    ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".html": "text/html; charset=utf-8",
    ".json": "application/json", ".svg": "image/svg+xml", ".webp": "image/webp", ".flac": "audio/flac",
    ".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".opus": "audio/ogg",
    ".mp4": "video/mp4", ".webm": "video/webm", ".woff2": "font/woff2", ".conv": "text/plain; charset=utf-8",
}
_RANGE = re.compile(r"bytes=(\d*)-(\d*)\Z")


class Stage:
    """A server for one page and one staged recording."""

    def __init__(self, staged: str | Path | None = None, *, page: str | Path | None = None,
                 port: int = 0) -> None:
        self.page = Path(page).resolve() if page is not None else PAGE
        if self.page.is_file():
            self.page, self.index = self.page.parent, self.page.name
        else:
            self.index = "index.html"
        if not (self.page / self.index).is_file():
            raise StageError(f"{self.page}: a page is a folder with an index.html in it, or an HTML file")
        self.staged = Path(staged).resolve() if staged is not None else None
        handler = type("Handler", (_Handler,), {"stage": self})
        try:
            self._server = ThreadingHTTPServer(("127.0.0.1", port), handler)
        except OSError as error:
            raise StageError(f"port {port} cannot be listened on: {error.strerror or error}") from None
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def address(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def url(self, page: str | None = None, *, capture: bool = False, still: bool = False,
            size: tuple[int, int] | None = None) -> str:
        """Where the page is, with what it is to follow.

        `page` is an address of a page served somewhere else; without it the
        page is this server's own. With `capture` the page is to run against
        the clock for a film, silently; with `still` it is to be held at one
        moment at a time, for a picture of each.
        """
        asks: dict[str, str] = {}
        if self.staged is not None:
            timeline = f"/{MOUNT}/timeline.json"
            asks["timeline"] = self.address + timeline if page else timeline
        if capture:
            asks["capture"] = "1"
        if still:
            asks["still"] = "1"
        if size:
            asks["size"] = f"{size[0]}x{size[1]}"
        base = page or f"{self.address}/{quote(self.index) if self.index != 'index.html' else ''}"
        if not asks:
            return base
        return base + ("&" if urlsplit(base).query else "?") + urlencode(asks, safe="/:")

    def start(self) -> Stage:
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._thread.is_alive():
            self._server.shutdown()
        self._server.server_close()

    def __enter__(self) -> Stage:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def find(self, path: str) -> Path | None:
        """The file a request path means, or None when there is none or it reaches outside."""
        parts = [part for part in unquote(path).split("/") if part not in ("", ".")]
        if ".." in parts:
            return None
        if parts[:1] == [MOUNT]:
            root, parts = self.staged, parts[1:]
        else:
            root = self.page
        if root is None:
            return None
        file = root.joinpath(*parts)
        if file.is_dir():
            file = file / (self.index if not parts else "index.html")
        if file.is_file():
            return file
        if parts and parts[-1] in SHARED and root is self.page:
            return PAGE / parts[-1]
        return None


class _Handler(BaseHTTPRequestHandler):
    stage: Stage
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - the name the standard library calls
        self._answer(body=True)

    def do_HEAD(self) -> None:  # noqa: N802
        self._answer(body=False)

    def _answer(self, *, body: bool) -> None:
        file = self.stage.find(urlsplit(self.path).path)
        if file is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        length = os.path.getsize(file)
        first, last, status = 0, length - 1, HTTPStatus.OK
        asked = _RANGE.match(self.headers.get("Range") or "")
        if asked and (asked[1] or asked[2]) and length:
            if asked[1]:
                first, last = int(asked[1]), min(int(asked[2]) if asked[2] else length - 1, length - 1)
            else:
                first = max(0, length - int(asked[2]))
            if first > last:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{length}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = HTTPStatus.PARTIAL_CONTENT
        self.send_response(status)
        kind = _KINDS.get(file.suffix.lower()) or mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(last - first + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        # A page served somewhere else may read the timeline and the sound.
        self.send_header("Access-Control-Allow-Origin", "*")
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {first}-{last}/{length}")
        self.end_headers()
        if not body:
            return
        with open(file, "rb") as source:
            source.seek(first)
            left = last - first + 1
            try:
                while left > 0 and (piece := source.read(min(left, 1 << 16))):
                    self.wfile.write(piece)
                    left -= len(piece)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - the name the standard library uses
        pass
