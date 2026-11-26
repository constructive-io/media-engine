"""The `me` command line.

Each group of commands lives in its own module and adds itself to the parser
with a `register(commands)` function. A command is a function that takes the
parsed arguments, prints what it did, and returns an exit status or None.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from .. import __version__
from ..adapters import AdapterError
from ..conv import ConvError
from ..db import DatabaseError
from ..edit import EditError
from ..ffmpeg import FfmpegError
from ..models import ModelError
from ..settings import SettingsError
from ..speakers import SpeakerError
from ..speech import SpeechError
from ..takes import TakeError
from ..text import TextError
from ..transcription import TranscriptionError
from . import analyze, cut, edit, library, listen, scripts, speech, system, takes

GROUPS = (library, analyze, listen, takes, edit, cut, scripts, speech, system)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="me", description="Constructive Media: one engine for video, images, audio and voice.",
    )
    root.add_argument("--version", action="version", version=f"constructive-media {__version__}")
    commands = root.add_subparsers(dest="command", required=True, metavar="command")
    for group in GROUPS:
        group.register(commands)
    return root


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        return arguments.run(arguments) or 0
    except (AdapterError, ConvError, DatabaseError, EditError, FfmpegError, ModelError, SettingsError,
            SpeakerError, SpeechError, TakeError, TextError, TranscriptionError) as error:
        print(f"me: {error}", file=sys.stderr)
        return 1
    except ModuleNotFoundError as error:
        print(f"me: this command needs the {error.name!r} package, which is not installed. "
              "See the README for what to install.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
