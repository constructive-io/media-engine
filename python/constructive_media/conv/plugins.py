"""The plugins a script is read with unless others are given.

    effects   reverb, delay and compression, on a character, a line, a span or a track
    slides    `[slide ID]`, `[slide ID step:N]`: a deck shows that slide from here
    speakers  `[speakers faces captions]`: what a player shows for whoever is speaking
    video     `[video CLIP]`: a clip takes the screen and the scene waits for it

A slide and a speakers marker are silent and take no time. A renderer records
when each one falls.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any

from .schema import Plugin, named, number, text, yes_no
from .values import did_you_mean

REVERBS = ("room", "hall", "cathedral", "plate")

# What a player can show for whoever is speaking.
WIDGETS = ("faces", "lower-third", "waveform", "captions")
_ALWAYS = "lower-third:always"
# `faces` leaves the mouth alone; `faces:talking` moves it with the voice.
_TALKING = "faces:talking"
_SAME = {"lower-third:first": "lower-third", "faces:still": "faces"}


def effects(reverbs: Sequence[str] = REVERBS) -> Plugin:
    """Effect keys. `reverbs` are the rooms a renderer offers."""
    keys = {
        "reverb": named("reverb"), "reverb_mix": number(0, 1), "delay": number(0.01, 5),
        "delay_feedback": number(0, 0.95), "delay_mix": number(0, 1), "compress": yes_no(),
    }
    return Plugin(name="effects", enums={"reverb": tuple(reverbs)},
                  keys={"character": keys, "line": keys, "span": keys, "track": keys})


def _slide(said: str, wrong: Callable[[str], None]) -> dict[str, Any]:
    slide, step, bad = None, 0, False
    for word in filter(None, re.split(r"[\s,]+", said)):
        key, colon, value = word.partition(":")
        if colon:
            if key != "step":
                wrong(f"`{key}:` isn't a slide option — use `[slide ID]` or `[slide ID step:N]`"
                      + did_you_mean(key, ["step"]))
                bad = True
            elif not re.match(r"[0-9]+\Z", value):
                wrong(f"`step:{value}` — step is a whole number of builds, e.g. `step:1`")
                bad = True
            else:
                step = int(value)
        elif slide is None and re.match(r"[a-z0-9][a-z0-9_-]*\Z", word):
            slide = word
        else:
            wrong(f"`{word}` — a slide id is lowercase, like the deck's ids (`title`, `market-opportunity`)"
                  if slide is None else f"`{word}` — one slide per marker; put the next on its own line")
            bad = True
    if slide is None and not bad:
        wrong("`[slide]` needs the slide's id, e.g. `[slide problem]` or `[slide problem step:1]`")
    return {"id": slide, "step": step}


def slides() -> Plugin:
    return Plugin(name="slides", directives={"slide": _slide})


def _speakers(said: str, wrong: Callable[[str], None]) -> dict[str, Any]:
    choices = (*WIDGETS, _ALWAYS, _TALKING, "all", "off")
    want = [_SAME.get(word, word) for word in re.split(r"[\s,+]+", said.lower()) if word]
    if not want:
        wrong("`[speakers]` needs what to show, e.g. `[speakers faces lower-third]`, `[speakers all]` "
              f"or `[speakers off]` — widgets: {', '.join(WIDGETS)}")
    for word in want:
        if word not in choices:
            wrong(f"`{word}` isn't a speaker widget" + did_you_mean(word, choices)
                  + f" — widgets: {', '.join(WIDGETS)} (`{_ALWAYS}` to show it on every line, "
                  f"`{_TALKING}` for a face that moves its mouth with the voice), or `all` / `off`")
    if ("off" in want or "all" in want) and len(want) > 1:
        wrong("`off` and `all` stand alone: `[speakers off]`, `[speakers all]`")
    if want == ["off"]:
        show: list[str] = []
    elif want == ["all"]:
        show = list(WIDGETS)
    else:
        show = [widget for widget in (*WIDGETS, _ALWAYS, _TALKING) if widget in want]
    # The fuller form of a widget stands for it.
    for fuller, widget in ((_ALWAYS, "lower-third"), (_TALKING, "faces")):
        if fuller in show:
            show = [shown for shown in show if shown != widget]
    return {"show": show}


def speakers() -> Plugin:
    """The speakers marker. A character also gains `name:` and `title:`, for the lower third."""
    return Plugin(name="speakers", keys={"character": {"name": text(), "title": text()}},
                  directives={"speakers": _speakers})


_CLIP_TAKES = ("hold", "start", "for", "volume")


def _clip(item: dict[str, Any], wrong: Callable[[str], None]) -> None:
    settings = item["overrides"]
    other = [key for key in settings if key not in _CLIP_TAKES]
    if item["action"] != "play" or not item["sound"]:
        wrong("`[video …]` plays one clip, e.g. `[video COVER]` — it can't be stopped or set; a clip "
              "plays its length (trim with `start:`/`for:`)")
    elif "at" in settings:
        wrong("a video takes the screen, so it can't overlap anything — drop `at:`")
    elif other:
        wrong(f"`{', '.join(other)}` doesn't apply to a video clip — it takes `start:`, `for:` and `vol:`")
    settings["hold"] = True


def _clips_are_files(scene: dict[str, Any], problem: Callable[[dict[str, Any], str], None]) -> None:
    for item in scene["items"]:
        if item["type"] != "track" or item["track"] != "video" or not item["sound"]:
            continue
        sound = scene["sounds"].get(item["sound"])
        if sound is not None and not sound.get("file"):
            problem(item, f"`{item['sound']}` is generated (`prompt:`), but `[video …]` needs a video file: "
                          f"`{item['sound']}: {{ file: path/to/clip.mp4 }}`")


def video() -> Plugin:
    """A `video` track every scene has. What plays on it must be a file."""
    return Plugin(name="video", tracks=("video",), track_rules={"video": _clip}, finalize=_clips_are_files)


def defaults() -> tuple[Plugin, ...]:
    return effects(), slides(), speakers(), video()
