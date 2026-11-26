"""Commands about the engine itself: status and adapters."""

from __future__ import annotations

import argparse
from typing import Any

from .. import adapters, db
from ..settings import Settings

#: What `status` counts. Each is a table one of the schema modules creates.
COUNTED = (
    ("personas", "media.personas"),
    ("assets", "media.assets"),
    ("takes", "media.takes"),
    ("voices", "media.voices"),
    ("shots", "media_video.shots"),
    ("frames", "media_video.frames"),
    ("detections", "media_video.detections"),
    ("segments", "media_video.segments"),
    ("words", "media_video.words"),
    ("speakers", "media_video.speaker_clusters"),
    ("passages", "media_video.passages"),
    ("flags", "media_video.flags"),
    ("scores", "media_video.scores"),
    ("timelines", "media_video.timelines"),
    ("decisions", "media_video.decisions"),
    ("renders", "media_audio.renders"),
)


def register(commands: Any) -> None:
    command = commands.add_parser("status", help="show where the engine keeps things and what it holds")
    command.set_defaults(run=run_status)

    command = commands.add_parser("adapters", help="list every slot and the adapters that can fill it")
    command.set_defaults(run=run_adapters)


def run_status(_arguments: argparse.Namespace) -> None:
    settings = Settings.from_env()
    database = settings.database
    print(f"  {'database':<12} {database.name} on {database.host}:{database.port}")
    print(f"  {'work':<12} {settings.work_dir}")
    cur = db.cursor()
    cur.execute("SELECT table_schema || '.' || table_name FROM information_schema.tables "
                "WHERE table_schema IN ('media', 'media_video', 'media_audio')")
    present = {row[0] for row in cur.fetchall()}
    for label, table in COUNTED:
        if table not in present:
            print(f"  {label:<12} not deployed")
            continue
        cur.execute(f"SELECT count(*) FROM {table}")  # noqa: S608 - names come from COUNTED
        print(f"  {label:<12} {cur.fetchone()[0]}")


def run_adapters(_arguments: argparse.Namespace) -> None:
    registry = adapters.registry()
    settings = Settings.from_env()
    for slot, fills in adapters.SLOTS.items():
        available = registry.available(slot)
        print(f"{slot}: {fills}")
        if not available:
            print("  (nothing installed)")
            continue
        try:
            chosen = registry.describe(slot, settings=settings).name
        except adapters.AdapterError:
            chosen = None
        for adapter in available:
            mark = "*" if adapter.name == chosen else " "
            print(f" {mark} {adapter.name:<14} {adapter.license:<14} {adapter.origin:<10} {adapter.summary}")
