---
name: constructive-media
description: Constructive Media — one engine for video, images, audio and voice, driven by the `me` command line with its state in PostgreSQL. Use when asked to "render a .conv", "turn a script into audio", "make a voiced scene", "say this script", "give a persona a voice", "set up the media engine", "ingest footage", "transcribe recordings", "find the best takes", "make a cut", "render a cut", or to choose a model, an adapter or a plugin for the engine.
compatibility: Python 3.11+, PostgreSQL 17+ with pgvector, pgpm, ffmpeg, whisper.cpp
metadata:
  author: constructive-io
  version: "0.1.0"
---

# Constructive Media

One engine for video, images, audio and voice. Everything it knows is kept in a
PostgreSQL database, everything it does is a `me` command, and every model it
uses is reached through an adapter you can swap.

It is the renderer for the [`.conv` format](https://github.com/constructive-io/conversation-spec):
a plain-text script of who speaks, what they say and what is heard around them
goes in, and a mixed recording comes out.

## When to apply

- **A script to sound:** writing a `.conv` script, checking it, giving its
  personas voices, rendering it, saying one line again.
- **Footage to a cut:** ingesting recordings, transcribing them, telling voices
  apart, ranking what is worth using, assembling and rendering a cut. See
  [references/footage.md](references/footage.md).
- **Setting up:** the database, the speech server, the settings, an adapter or
  a plugin.

## Set up, once

```sh
git clone https://github.com/constructive-io/constructive-media
cd constructive-media
pnpm install

# 1. A database. Docker, or skip these two lines and export PGHOST/PGPORT/PGUSER/PGPASSWORD
pgpm docker start
eval "$(pgpm env)"
pgpm deploy --database media --createdb --yes --package media-video
pgpm deploy --database media --yes --package media-audio

# 2. The engine, and the two programs it runs
pip install ".[audio]"            # ".[all]" for footage as well
brew install ffmpeg whisper.cpp   # or your system's packages

# 3. Where the database is
export CONSTRUCTIVE_MEDIA_DATABASE_URL=postgres://postgres:password@localhost:5432/media

me status                         # the database it found and what it holds
```

Never stop a PostgreSQL that is already running to make room for Docker: use
it, by exporting the `PG*` variables for it.

## A script to sound

**1. Write the script.** Save this as `scene.conv`
(the same scene is in [`examples/scene.conv`](../../../examples/scene.conv)):

```conv
@characters
ADA:   { persona: ada }
GRACE: { persona: grace, reverb: room }
---
ADA: The lantern is lit at dusk.
GRACE: Then we should leave before the tide comes in.
[pause 0.5]
ADA[whisper]: Bring the map, <break 0.3> and the small brass key.
```

**2. Check it.** No database and no model is needed for this:

```sh
me conv scene.conv            # ok, or every problem with its line and how to mend it
me conv scene.conv --json     # the scene the script describes
```

**3. Point the engine at a speech server.** Any server that answers the usual
`audio/speech` request will do, on your machine or hosted:

```sh
export CONSTRUCTIVE_MEDIA_URL_SPEECH=http://localhost:8080/v1
export CONSTRUCTIVE_MEDIA_MODEL_SPEECH=the-name-the-server-knows-its-model-by
export CONSTRUCTIVE_MEDIA_KEY_SPEECH=...     # only if the server wants a key
```

No speech server yet? On a Mac, `python3 examples/mac-voice-server.py` speaks
with the system's own voice, which is enough to hear a script end to end. Use
`CONSTRUCTIVE_MEDIA_MODEL_SPEECH=say` with it.

**4. Give everybody a voice.** A voice is a name the speech server knows, kept
against a persona. No recording is stored:

```sh
me voice ada   the-servers-name-for-a-voice --new
me voice grace another-of-its-voices --new
me voice ada   a-quieter-voice --emotion whisper    # optional: one voice per emotion
me voices
```

**5. Render.**

```sh
me say scene.conv out/scene.flac --stems
```

```
out/scene.flac                  the mix, at -16 LUFS
out/scene.lines/line_001.wav    every line as it was said
out/scene.stems/dialogue.wav    each track on its own, with --stems
out/scene.manifest.json         each line's seed, takes and verdict, and where everything landed
```

Each line is said, listened back to with Whisper, compared with its words, and
said again with another seed until a take passes. The first render downloads
the Whisper model (about 1.6 GB) into `~/.constructive-media/models`.

**6. Change one thing.** Edit the script, then say again only what changed.
Every other line is kept as it was:

```sh
me say scene.conv out/scene.flac --only grace     # one persona's lines
me say scene.conv out/scene.flac --only 2,3       # lines by number, as in out/scene.lines/
```

## Writing `.conv`

The grammar is [SPEC.md in conversation-spec](https://github.com/constructive-io/conversation-spec/blob/main/SPEC.md),
and that repository has a `conv` skill for writing scripts. The shape of it:

| Write | To get |
| --- | --- |
| `ADA: { persona: ada, emotion: warm, reverb: room }` under `@characters` | a character, the persona whose voice speaks it, and how it sounds by default |
| `RAIN: { file: sounds/rain.wav, loop: true, track: ambience }` under `@sounds` | a sound, from a file beside the script |
| `---` | the end of the declarations and the start of the scene |
| `ADA: Did you hear that?` | a line |
| `ADA[whisper, seed:207, reverb:hall]: ...` | a line with its own emotion, take and effects |
| `<break 0.4>` inside a line | a silence in the middle of it |
| `[pause 1.5]` | a silence between lines |
| `[RAIN]`, `[CREAK hold]` | a sound; `hold` makes the scene wait for it |
| `[music BED fade-in:3]`, `[music off]` | a sound started or stopped on a track |

Names of characters and sounds are UPPERCASE, tracks are lowercase. Effects are
`reverb` (`room`, `hall`, `cathedral`, `plate`), `reverb-mix`, `delay`,
`delay-feedback`, `delay-mix`, `compress` and `gain`, on a character, a line or
a track.

Two things this renderer asks of a script:

- **A sound needs a `file:`.** A sound described by a `prompt:` cannot be made
  yet. `me say --without-generated` leaves those out instead of stopping.
- **Sound files are yours.** They are looked for beside the script, then in
  each folder given with `--sounds`.

## When something goes wrong

| It says | Do |
| --- | --- |
| `could not connect to database` | Export `CONSTRUCTIVE_MEDIA_DATABASE_URL`, and deploy the two modules into that database |
| `nothing says where the speech model is` | Export `CONSTRUCTIVE_MEDIA_URL_SPEECH` and `CONSTRUCTIVE_MEDIA_MODEL_SPEECH` |
| `` `GRACE` speaks as `grace`, and `grace` has no voice `` | `me voice grace <the server's name for a voice> --new` |
| the speech server refuses the request | It may not take a `seed`. `export CONSTRUCTIVE_MEDIA_EXTRA_SPEECH='{"seed": null}'` |
| a line is marked `needs_review` | No take of it passed. Listen to `out/scene.lines/`, reword the line, or raise `--attempts` |
| `this command needs the '...' package` | Install the extra it names: `pip install ".[all]"` |

`me status` shows where things are kept; `me adapters` shows every slot and
what fills it; `me <command> --help` describes every command.

## Settings

Environment variables, all starting `CONSTRUCTIVE_MEDIA_`:

| Variable | For |
| --- | --- |
| `DATABASE_URL` | `postgres://user:pass@host:port/name`. Without it the `PG*` variables, and a database called `media` |
| `HOME` | Where everything is written. `~/.constructive-media` unless you say |
| `DEVICE` | `auto`, `cpu`, `mps` or `cuda` |
| `ADAPTER_<SLOT>` | Which adapter fills a slot, such as `ADAPTER_EFFECTS=ffmpeg` |
| `MODEL_<SLOT>`, `URL_<SLOT>`, `KEY_<SLOT>`, `EXTRA_<SLOT>` | The model, server, key and extra request fields for a slot |

Never print or commit a key. Say where it is kept, not what it is.

## Adapters, models and plugins

- Every slot and what an adapter for it must do: [docs/ADAPTERS.md](../../../docs/ADAPTERS.md).
- Every model the engine knows by name, with its source and license: [docs/MODELS.md](../../../docs/MODELS.md).
- The engine holds only MIT, Apache-2.0 and BSD code: [docs/LICENSING.md](../../../docs/LICENSING.md).
  An adapter for a tool under a copyleft license is a separate package in
  [media-engine-plugins](https://github.com/constructive-io/media-engine-plugins),
  which has a skill of its own (`media-engine-plugins`).

## Related skills

| Skill | Where | For |
| --- | --- | --- |
| `conv` | [conversation-spec](https://github.com/constructive-io/conversation-spec) | Writing and checking `.conv` scripts |
| `media-engine-plugins` | [media-engine-plugins](https://github.com/constructive-io/media-engine-plugins) | Installing, choosing and writing plugins |
| `pgpm` | [`.agents/skills/pgpm`](../pgpm/SKILL.md) | The database modules: deploy, revert, verify, test |
