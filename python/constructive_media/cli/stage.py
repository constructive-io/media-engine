"""Commands for the stage: a render watched as a page, and filmed."""

from __future__ import annotations

import argparse
import sys
from typing import Any


def register(commands: Any) -> None:
    command = commands.add_parser(
        "film", help="film the page that follows a render, as a video",
        description="Turn a render into a video. The page is opened in a browser with no window and run "
                    "against the render's clock: it shows each slide where the script's `[slide …]` falls, "
                    "reveals its steps, draws whoever is speaking as `[speakers …]` asks, and each "
                    "`[video …]` clip is put in from its own frames. The sound goes under it. Filming is in "
                    "real time. With no page, a plain one is filmed: the speakers and what they say.",
    )
    command.add_argument("manifest", help="the manifest `me say` wrote, or the sound it is beside")
    command.add_argument("output", help="the video to write, such as out/scene.mp4")
    _page(command)
    command.add_argument("--url", metavar="ADDRESS",
                         help="film a page served somewhere else, which keeps the contract in docs/STAGE.md")
    command.add_argument("--size", default="1920x1080", metavar="WxH", help="how big the film is (default: 1920x1080)")
    command.add_argument("--fps", type=int, default=30, metavar="N", help="frames a second (default: 30)")
    command.add_argument("--bitrate", metavar="RATE",
                         help="bits a second for the video, such as 2M or 800k (default: about 8M at 1920x1080, "
                              "which keeps small text sharp; a quarter of it is a quarter of the file)")
    command.add_argument("--keep", metavar="FOLDER", help="leave the timeline, the frames and the clips here")
    command.set_defaults(run=run_film)

    command = commands.add_parser(
        "stage", help="watch the page that follows a render, in your own browser",
        description="Serve a page with a render's timeline and sound, and say where it is. Open the address "
                    "to watch it with the sound: space plays and stops, the arrows go from slide to slide. "
                    "It is the same page `me film` films. It serves this machine only, until it is stopped.",
    )
    command.add_argument("manifest", nargs="?", help="the manifest `me say` wrote, or the sound it is beside; "
                                                     "without one the page is stepped through by hand")
    _page(command)
    command.add_argument("--port", type=int, default=0, metavar="N", help="the port to serve on (default: any free one)")
    command.add_argument("--write", metavar="FOLDER",
                         help="serve nothing: write the timeline, the sound, the clips and the faces here, for a "
                              "page that is built and served by something else")
    command.add_argument("--size", default="1920x1080", metavar="WxH",
                         help="how big the clips are cut (default: 1920x1080)")
    command.add_argument("--fps", type=int, default=30, metavar="N", help="frames a second for the clips (default: 30)")
    command.set_defaults(run=run_stage)

    command = commands.add_parser(
        "print", help="write a page as a PDF",
        description="Open a page in a browser with no window and write it as a PDF, laid out as the page's "
                    "own print styles ask. A document comes out as its pages; a deck as it is told to.",
    )
    command.add_argument("page", help="a folder with an index.html, an HTML file, or an address")
    command.add_argument("output", help="the PDF to write")
    command.set_defaults(run=run_print)


def _page(command: argparse.ArgumentParser) -> None:
    command.add_argument("--page", metavar="FOLDER",
                         help="the page to show: a folder with an index.html, or an HTML file (default: the plain page)")
    command.add_argument("--faces", metavar="FOLDER",
                         help="pictures named for personas, such as ada.png (default: a `faces` folder in the page's; "
                              "whoever has none there gets the one a library added has for them)")


def _size(written: str) -> tuple[int, int] | None:
    width, _, height = written.lower().partition("x")
    if width.isdigit() and height.isdigit():
        return int(width), int(height)
    print(f"me: {written}: a size is a width and a height, such as 1920x1080", file=sys.stderr)
    return None


def run_film(arguments: argparse.Namespace) -> int | None:
    from .. import libraries
    from ..stage import film

    size = _size(arguments.size)
    if size is None:
        return 2
    report = film.film(arguments.manifest, arguments.output, page=arguments.page, url=arguments.url, size=size,
                       fps=arguments.fps, faces=arguments.faces, more_faces=libraries.faces(), keep=arguments.keep,
                       bitrate=arguments.bitrate, tell=print)
    print(f"{report['seconds']:.1f} seconds, {report['slides']} slides, {report['clips']} clips, "
          f"{report['frames']} pictures, {report['bytes'] / 1_000_000:.1f} MB: {arguments.output}")
    if report["notes"]:
        print(f"{len(report['notes'])} to look at, above")
    return None


def run_stage(arguments: argparse.Namespace) -> int | None:
    import tempfile
    import time
    from pathlib import Path

    from .. import libraries
    from ..stage import serve, timeline

    size = _size(arguments.size)
    if size is None:
        return 2
    faces = arguments.faces
    if faces is None and arguments.page:
        page = Path(arguments.page)
        beside = (page if page.is_dir() else page.parent) / "faces"
        faces = beside if beside.is_dir() else None
    if arguments.write:
        if not arguments.manifest:
            print("me: --write needs the render whose timeline is written", file=sys.stderr)
            return 2
        made = timeline.build(arguments.manifest, arguments.write, size=size, fps=arguments.fps, faces=faces,
                              more_faces=libraries.faces(), tell=print)
        print(f"{made['seconds']:.1f} seconds, {len(made['lines'])} lines, {len(made['slides'])} slides, "
              f"{len(made['clips'])} clips: {Path(arguments.write) / 'timeline.json'}")
        return None
    with tempfile.TemporaryDirectory(prefix="stage-") as scratch:
        staged = None
        if arguments.manifest:
            staged = Path(scratch) / "stage"
            timeline.build(arguments.manifest, staged, size=size, fps=arguments.fps, faces=faces,
                           more_faces=libraries.faces(), tell=print)
        with serve.Stage(staged, page=arguments.page, port=arguments.port) as stage:
            print(stage.url(), flush=True)
            print("stop with Ctrl-C", flush=True)
            try:
                while True:
                    time.sleep(3600)
            except KeyboardInterrupt:
                print()
    return None


def run_print(arguments: argparse.Namespace) -> int | None:
    from pathlib import Path

    from .. import browser
    from ..stage import serve

    if "://" in arguments.page:
        browser.browser().print(arguments.page, Path(arguments.output))
    else:
        with serve.Stage(page=arguments.page) as stage:
            browser.browser().print(stage.url(), Path(arguments.output))
    print(arguments.output)
    return None
