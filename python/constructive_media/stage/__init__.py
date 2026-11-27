"""The stage: a page that follows a recording, and a film of it.

`me say` turns a script into sound and writes down when everything in it
fell: each line, each `[slide …]`, each `[speakers …]`, each `[video …]`.
The stage turns that into pictures.

    timeline   what fell when, as one file a page can read
    serve      a page, the timeline and the sound, served to a browser
    film       the page watched from start to end, and written as a video

A page is any HTML. It follows the recording by loading `stage.js`, which
shows the slide the script asked for, reveals its steps, draws the captions
and whoever is speaking, and plays the clips. With no page at all there is a
plain one built in, so any script can be filmed.

Filming does not play the sound. The page is run against the clock, every
picture the browser paints is kept with its time, and the pictures are laid
on the recording's own clock with the sound under them. A `[video …]` clip is
not filmed: its own frames are put in, so it comes out as it went in.

The contract a page keeps is in docs/STAGE.md.
"""

from __future__ import annotations

from pathlib import Path


class StageError(RuntimeError):
    """A recording cannot be staged or filmed as asked."""


#: The built-in page and the player every page loads.
PAGE = Path(__file__).parent / "page"
#: Under this path a server answers with the timeline and what it names.
MOUNT = "_stage"
