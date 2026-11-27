"""Chrome, or any Chromium, run with no window and driven down a pipe.

The browser is started with `--remote-debugging-pipe` and spoken to in the
DevTools protocol: one JSON message after another, each ended by a zero byte,
written to its file descriptor 3 and read from its file descriptor 4. No port
is opened and nothing is installed: this is the standard library and a browser
that is already on the machine.

Which browser is found by looking in the usual places. To name one, set
`CONSTRUCTIVE_MEDIA_MODEL_BROWSER` to the program.

Filming is in real time. The browser sends a picture whenever the page
changes, with the time it painted it, and each is kept as a JPEG. A page that
runs for a minute takes a minute to film.
"""

from __future__ import annotations

import base64
import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
import time
from collections import deque
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..settings import ENV_PREFIX, Settings
from . import BrowserError, Filmed, Frame

#: Where a browser usually is, best first: a path, or a name to look for.
CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome",
    "microsoft-edge", "brave-browser",
)
FLAGS = (
    "--headless=new", "--remote-debugging-pipe", "--no-first-run", "--no-default-browser-check",
    "--disable-extensions", "--disable-background-networking", "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows", "--hide-scrollbars",
    "--mute-audio", "--force-device-scale-factor=1", "--autoplay-policy=no-user-gesture-required",
)
#: How long a page has to load and say it has started.
READY_S = 45.0
#: How long past its own length a page has to say it is done.
SLACK_S = 60.0
#: How long one answer from the browser is waited for.
ANSWER_S = 30.0
QUALITY = 90

# The browser wants its pipe on descriptors 3 and 4. This puts it there and
# becomes the browser: a program of its own, so nothing is changed in the
# engine's process between a fork and an exec.
_LAUNCH = (
    "import os, sys\n"
    "reads, writes = int(sys.argv[1]), int(sys.argv[2])\n"
    "os.dup2(reads, 3); os.dup2(writes, 4); os.close(reads); os.close(writes)\n"
    "os.execv(sys.argv[3], sys.argv[3:])\n"
)
_STATE = "JSON.stringify(window.__stage || null)"
_SETTLED = ("Promise.all([document.fonts ? document.fonts.ready : null, new Promise(function (done) {"
            " if (document.readyState === 'complete') done(); else addEventListener('load', done); })])"
            ".then(function () { return true; })")


class Chrome:
    """A Chrome or Chromium on this machine."""

    name = "chrome"

    def __init__(self, program: str | None = None, *, settings: Settings | None = None) -> None:
        settings = settings or Settings.from_env()
        self.program = _found(program or settings.model("browser"))
        self.flags = _flags(settings.extra("browser"))

    def film(self, url: str, folder: Path, *, size: tuple[int, int], seconds: float,
             tell: Callable[[str], None] | None = None) -> Filmed:
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        width, height = size
        frames: list[Frame] = []
        notes: list[str] = []
        start: float | None = None
        with self._tab(size) as tab:
            tab.call("Runtime.enable")
            tab.call("Page.startScreencast", {"format": "jpeg", "quality": QUALITY, "maxWidth": width,
                                              "maxHeight": height, "everyNthFrame": 1})
            tab.call("Page.navigate", {"url": url})
            asked: int | None = None
            ask_at = 0.0
            deadline = time.monotonic() + READY_S
            while True:
                now = time.monotonic()
                if now > deadline:
                    raise BrowserError(
                        "the page never said it had started: it must load stage.js, or set window.__stage itself"
                        if start is None else "the page never said it was done")
                if asked is None and now >= ask_at:
                    asked = tab.send("Runtime.evaluate", {"expression": _STATE, "returnByValue": True})
                    ask_at = now + 0.1
                message = tab.read(0.05)
                if message is None:
                    continue
                if asked is not None and message.get("id") == asked:
                    asked = None
                    state = _value(message)
                    if state and state.get("error"):
                        raise BrowserError(f"the page could not start: {state['error']}")
                    if state and start is None:
                        start = float(state["startEpoch"]) / 1000.0
                        deadline = time.monotonic() + float(state.get("seconds") or seconds) + SLACK_S
                        if tell:
                            tell("  the page has started")
                    if state and state.get("done"):
                        break
                elif message.get("method") == "Page.screencastFrame":
                    params = message["params"]
                    file = folder / f"frame_{len(frames) + 1:06d}.jpg"
                    file.write_bytes(base64.b64decode(params["data"]))
                    frames.append(Frame(float(params["metadata"]["timestamp"]), file))
                    tab.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
                elif message.get("method") == "Runtime.exceptionThrown":
                    notes.append(_thrown(message["params"]))
            tab.send("Page.stopScreencast")
        assert start is not None
        return Filmed(start, frames, notes)

    def stills(self, url: str, moments: Sequence[tuple[float, Path]], *, size: tuple[int, int],
               tell: Callable[[str], None] | None = None) -> list[str]:
        notes: list[str] = []
        with self._tab(size) as tab:
            tab.call("Runtime.enable")
            tab.call("Page.navigate", {"url": url})
            deadline = time.monotonic() + READY_S
            while True:
                state = _value({"result": tab.call("Runtime.evaluate", {"expression": _STATE, "returnByValue": True})})
                if state and state.get("error"):
                    raise BrowserError(f"the page could not start: {state['error']}")
                if state and state.get("still"):
                    break
                if time.monotonic() > deadline:
                    raise BrowserError("the page never said it could be held still: it must load stage.js, or set "
                                       "window.__stage itself")
                time.sleep(0.05)
            for at, file in moments:
                tab.call("Runtime.evaluate", {"expression": f"window.__stage.at({float(at):.4f})",
                                              "awaitPromise": True, "returnByValue": True})
                picture = tab.call("Page.captureScreenshot", {"format": "png"})
                file = Path(file)
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(base64.b64decode(picture["data"]))
                if tell:
                    tell(f"  {file.name}")
            state = _value({"result": tab.call("Runtime.evaluate", {"expression": _STATE, "returnByValue": True})}) or {}
            notes += [str(note) for note in state.get("notes") or []]
            while (message := tab.read(0.0)) is not None:
                if message.get("method") == "Runtime.exceptionThrown":
                    notes.append(_thrown(message["params"]))
        return list(dict.fromkeys(notes))

    def print(self, url: str, target: Path) -> Path:
        target = Path(target)
        with self._tab((1280, 960)) as tab:
            tab.call("Page.navigate", {"url": url})
            tab.call("Runtime.evaluate", {"expression": _SETTLED, "awaitPromise": True, "returnByValue": True})
            printed = tab.call("Page.printToPDF", {"printBackground": True, "preferCSSPageSize": True},
                               timeout=120.0)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(printed["data"]))
        return target

    @contextmanager
    def _tab(self, size: tuple[int, int]) -> Iterator[_Tab]:
        width, height = size
        with tempfile.TemporaryDirectory(prefix="browser-") as profile:
            flags = [*FLAGS, *self.flags, f"--window-size={width},{height}", f"--user-data-dir={profile}"]
            if hasattr(os, "geteuid") and os.geteuid() == 0:
                flags.append("--no-sandbox")  # a browser will not start its sandbox as root
            pipe = _Pipe(self.program, [*flags, "about:blank"])
            try:
                target = pipe.call("Target.createTarget", {"url": "about:blank"})["targetId"]
                session = pipe.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
                tab = _Tab(pipe, session)
                tab.call("Page.enable")
                tab.call("Emulation.setDeviceMetricsOverride",
                         {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False})
                yield tab
            finally:
                pipe.close()


class _Pipe:
    """The two ends of the pipe a browser is spoken to down."""

    def __init__(self, program: str, arguments: list[str]) -> None:
        if os.name != "posix":
            raise BrowserError("filming a page needs macOS or Linux")
        import fcntl

        down_read, down_write = os.pipe()
        up_read, up_write = os.pipe()
        # Clear of 3 and 4, which is where the browser's ends are put.
        reads = fcntl.fcntl(down_read, fcntl.F_DUPFD, 10)
        writes = fcntl.fcntl(up_write, fcntl.F_DUPFD, 10)
        os.close(down_read)
        os.close(up_write)
        try:
            self.process = subprocess.Popen(
                [sys.executable, "-c", _LAUNCH, str(reads), str(writes), program, *arguments],
                pass_fds=(reads, writes), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        finally:
            os.close(reads)
            os.close(writes)
        self._out, self._in = down_write, up_read
        self._held = bytearray()
        self._seen = 0
        self._number = 0
        self.waiting: deque[dict[str, Any]] = deque()

    def send(self, method: str, params: dict[str, Any] | None = None, session: str | None = None) -> int:
        self._number += 1
        message: dict[str, Any] = {"id": self._number, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        data = memoryview(json.dumps(message).encode() + b"\0")
        try:
            while data:
                data = data[os.write(self._out, data):]
        except OSError:
            raise BrowserError("the browser stopped") from None
        return self._number

    def read(self, timeout: float) -> dict[str, Any] | None:
        """The next message from the browser, or None when none came in time."""
        if self.waiting:
            return self.waiting.popleft()
        return self._next(timeout)

    def call(self, method: str, params: dict[str, Any] | None = None, session: str | None = None, *,
             timeout: float = ANSWER_S) -> dict[str, Any]:
        """Ask, and wait for the answer. Whatever else arrives meanwhile is kept for `read`."""
        number = self.send(method, params, session)
        deadline = time.monotonic() + timeout
        while (left := deadline - time.monotonic()) > 0:
            message = self._next(left)
            if message is None:
                break
            if message.get("id") == number:
                if "error" in message:
                    raise BrowserError(f"the browser refused {method}: {message['error'].get('message', 'no reason given')}")
                return message.get("result") or {}
            self.waiting.append(message)
        raise BrowserError(f"the browser did not answer {method}")

    def _next(self, timeout: float) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        while True:
            end = self._held.find(b"\0", self._seen)
            if end >= 0:
                message = json.loads(bytes(self._held[:end]))
                del self._held[:end + 1]
                self._seen = 0
                return message
            self._seen = len(self._held)
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([self._in], [], [], left)[0]:
                return None
            piece = os.read(self._in, 1 << 20)
            if not piece:
                raise BrowserError("the browser stopped before it had finished")
            self._held += piece

    def close(self) -> None:
        """Ask the browser to leave, hear it out, and see that it has gone."""
        try:
            self.send("Browser.close")
            ends = time.monotonic() + 5.0
            while time.monotonic() < ends:   # what it was still saying is read, so that it is not left waiting
                self._next(0.1)
        except BrowserError:
            pass
        for descriptor in (self._out, self._in):
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()


class _Tab:
    """One page of the browser: the pipe, and which page is meant."""

    def __init__(self, pipe: _Pipe, session: str) -> None:
        self.pipe, self.session = pipe, session

    def send(self, method: str, params: dict[str, Any] | None = None) -> int:
        return self.pipe.send(method, params, self.session)

    def call(self, method: str, params: dict[str, Any] | None = None, *, timeout: float = ANSWER_S) -> dict[str, Any]:
        return self.pipe.call(method, params, self.session, timeout=timeout)

    def read(self, timeout: float) -> dict[str, Any] | None:
        return self.pipe.read(timeout)


def _found(program: str | None) -> str:
    """The browser to run: the one named, or the first of the usual ones that is there."""
    for candidate in ([program] if program else CANDIDATES):
        path = candidate if os.path.isabs(candidate) else shutil.which(candidate)
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    if program:
        raise BrowserError(f"{program!r} is not a program that can be run")
    raise BrowserError("no Chrome or Chromium was found. Install one, or set "
                       f"{ENV_PREFIX}MODEL_BROWSER to the program")


def _flags(extra: str | None) -> list[str]:
    """More flags for the browser, from `EXTRA_BROWSER`: `{"flags": ["--no-sandbox"]}`."""
    if not extra:
        return []
    try:
        flags = json.loads(extra).get("flags", [])
    except (AttributeError, ValueError):
        flags = None
    if not isinstance(flags, list) or not all(isinstance(flag, str) for flag in flags):
        raise BrowserError(f'{ENV_PREFIX}EXTRA_BROWSER is written as {{"flags": ["--a-flag"]}}')
    return flags


def _value(message: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return json.loads(message["result"]["result"]["value"])
    except (KeyError, TypeError, ValueError):
        return None


def _thrown(params: dict[str, Any]) -> str:
    details = params.get("exceptionDetails") or {}
    described = (details.get("exception") or {}).get("description") or details.get("text") or "an error"
    return str(described).splitlines()[0]
