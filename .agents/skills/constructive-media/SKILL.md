---
name: constructive-media
description: Constructive Media — one engine for video, images, audio and voice, driven by the `media` command line with its state in PostgreSQL. Use when asked to "render a .conv", "turn a script into audio", "make a voiced scene", "say this script", "say this in a voice", "give a persona a voice", "clone a voice", "audition a voice", "blend two voices", "mix voices", "make a new voice from two", "make a narrated deck", "film a deck", "turn a script into a video", "make a walkthrough video", "narrate a document", "add a voice library", "set up the media engine", "ingest footage", "transcribe recordings", "find the best takes", "make a cut", "render a cut", "generate an image", "generate a video", "make a picture from a prompt", "talk to a model", "have a voice conversation", "measure the voice latency", or to choose a model, an adapter or a plugin for the engine.
compatibility: Python 3.11+, PostgreSQL 17+ with pgvector, pgpm, ffmpeg, whisper.cpp; Chrome or Chromium for video of a page
metadata:
  author: constructive-io
  version: "0.11.0"
---

# Constructive Media

One engine for video, images, audio and voice. Everything it knows is kept in a
PostgreSQL database, everything it does is a `media` command, and every model it
uses is reached through an adapter you can swap.

It is the renderer for the [`.conv` format](https://github.com/constructive-io/conversation-format):
a plain-text script of who speaks, what they say and what is heard around them
goes in, and a mixed recording comes out.

## When to apply

- **A script to sound:** writing a `.conv` script, checking it, giving its
  personas voices, rendering it, saying one line again.
- **A script to video:** filming a deck, a document or a walkthrough that
  follows the recording. See [A script to video](#a-script-to-video).
- **Voices:** saying a few words in a voice, cutting a clip to clone from,
  auditioning a voice, blending voices into a new one, sharing voices, sounds
  and faces as a library. See
  [Voices and libraries](#voices-and-libraries).
- **Footage to a cut:** ingesting recordings, transcribing them, telling voices
  apart, ranking what is worth using, assembling and rendering a cut. See
  [references/footage.md](references/footage.md).
- **Pictures and video from a description:** asking a model for one, and
  keeping what it made apart from what was filmed. See
  [Pictures and video from a description](#pictures-and-video-from-a-description).
- **Talking with a model:** a page that hears, answers and speaks, and how
  long it makes someone wait. See [Talk with a model](#talk-with-a-model).
- **Setting up:** the database, the speech server, the settings, an adapter or
  a plugin.

## How the pieces fit

| Piece | Where | What it does |
| --- | --- | --- |
| The `.conv` format | [conversation-format](https://github.com/constructive-io/conversation-format) | says what a script may hold; has the `conv` skill for writing one |
| `conv`, from `@conversation/cli` on npm | the same repository | checks a script and prints it as JSON. It makes no sound |
| `media`, this engine | here | checks a script with a parser of its own, then says and mixes it |
| Plugins | [media-engine-plugins](https://github.com/constructive-io/media-engine-plugins) | adapters under copyleft licenses, installed apart |
| Libraries | repositories of your own, started with `media init library NAME` from [media-engine-boilerplates](https://github.com/constructive-io/media-engine-boilerplates) | voices to clone, banks of sounds and faces, added with `media library add` |
| Projects | a workspace of your own, started with `media init` from [media-engine-boilerplates](https://github.com/constructive-io/media-engine-boilerplates) | a pnpm workspace whose packages are what `media film` records; `media init workspace NAME`, then `media init deck NAME` inside it, or `walkthrough`, `animation` or `document` |

`media` is the engine's command. The same command is installed as
`constructive-media`. Every command that speaks takes the voice by
name: there is no command that belongs to one voice.

`conv` and `media` do not call each other. They agree on what a script means
because the engine's parser is held to the format's own conformance files, kept
in [`tests/fixtures/conversation-format`](../../../tests/fixtures/conversation-format).
A script that `conv check` passes is a script `media conv` passes.

## Set up, once

```sh
git clone https://github.com/constructive-io/media-engine
cd media-engine
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

media status                      # the database it found and what it holds
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
media conv scene.conv         # ok, or every problem with its line and how to mend it
media conv scene.conv --json  # the scene the script describes
```

On a machine with Node.js and no engine, `npx @conversation/cli check scene.conv`
is the same check, from the format's own parser.

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
media voice ada   the-servers-name-for-a-voice --new
media voice grace another-of-its-voices --new
media voice ada   a-quieter-voice --emotion whisper  # optional: one voice per emotion
media voices
```

**5. Render.**

```sh
media say scene.conv out/scene.flac --stems
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
media say scene.conv out/scene.flac --only grace  # one persona's lines
media say scene.conv out/scene.flac --only 2,3    # lines by number, as in out/scene.lines/
```

## A script to video

`media say` writes down when every line and mark fell. `media film` opens a page in a
browser with no window, lets it follow the recording, and lays the pictures
under the sound. The whole of it is in [docs/STAGE.md](../../../docs/STAGE.md).

**1. Say what is on the screen, in the script.** These marks are part of the
format; a renderer of sound alone passes over them:

| Write | To get |
| --- | --- |
| `[slide pricing]` | the slide called `pricing` |
| `[slide pricing step:2]` | its steps up to 2 revealed |
| `[speakers faces lower-third waveform captions]` | what is drawn of whoever speaks |
| `[speakers off]` | nothing drawn of them |
| `[video DEMO]` | the clip `DEMO` takes the screen and is heard |
| `ADA: { persona: ada, name: Ada Marsh, title: Keeper }` | the words on her lower third |

**2. Make the page.** Any HTML that loads `stage.js` and `stage.css` (the
engine hands both over; a page never carries a copy) and marks its parts:

| In the page | Means |
| --- | --- |
| `<body data-stage="deck">` | one slide at a time. `document` scrolls to what is being read; `walkthrough` moves a pointer across a screen; `plain` leaves the page alone |
| `data-slide="pricing"` | what `[slide pricing]` shows |
| `data-step="2"` | revealed at `step:2` |
| `data-enter="left"` | how it arrives: `rise`, `fade`, `left`, `right`, `grow`, `pop`, `wipe`, or `draw` for an SVG stroke. `style="--stage-delay: 0.15s"` holds one back, `--stage-time` is how long it takes |
| `data-point="60%,40%"` | where a walkthrough's pointer goes |

A build per spoken line reads best: put `[slide ID step:N]` before the line
that speaks of it. `body[data-stage-speaker="ada"]` styles the page by who is
talking. What a page draws itself, a chart filling or a clock, is drawn from
`window.stage.since('pricing', 2)`, the seconds since the script reached that
step (null before it), so the film and the preview agree. The engine names
whoever speaks first at the bottom left: keep that corner of a slide clear.
All of it is in [docs/STAGE.md](../../../docs/STAGE.md).

There is a deck in [`examples/talk`](../../../examples/talk).

**3. Look at it, then film it.**

```sh
media say   examples/talk/talk.conv out/talk.flac
media still out/talk.flac out/stills --page examples/talk --sheet  # a picture of every beat, in seconds; --at pricing:2+0.4 for one moment
media film  out/talk.flac out/talk.mp4 --page examples/talk  # 1920x1080 at 30 a second, unless --size and --fps say; --bitrate 1M for a file a third the size
media stage out/talk.flac --page examples/talk              # the same page in your browser, to scrub through
media print examples/talk out/talk.pdf                      # the page as a PDF
```

Without `--page` the built-in page is filmed: whoever speaks, their words and
a waveform. Filming is in real time, a minute for a minute, and needs a Chrome
or Chromium that is already installed
(`CONSTRUCTIVE_MEDIA_MODEL_BROWSER` names one that is not found by itself).

**4. Look at it.** Read the pictures `media still` wrote (`out/stills/sheet_01.jpg`
and on, four beats to a sheet) after every change to the page, and fit the page
to the script that way: it takes seconds, and a film takes as long as the talk.
A still shows what the page draws from `window.stage` exactly as the film will;
a CSS animation or a timer of the page's own it shows only finished, which is
one more reason to draw from the script's clock. Both commands print anything
the page complained of. A slide the script names and the page does not have is
one of those. After filming, pull a frame out with
`ffmpeg -ss 12 -i out/talk.mp4 -frames:v 1 frame.png` and look at it before
saying the video is right.

## Voices and libraries

All of it is in [docs/VOICES.md](../../../docs/VOICES.md) and
[docs/LIBRARIES.md](../../../docs/LIBRARIES.md).

**Say a few words in a voice**, with no script:

```sh
media say --voice ada "The lantern is lit at dusk." out/line.wav
media say --voice ada --emotion whisper "Bring the map." out/quiet.wav
```

**Give a persona a voice to clone**, where the speech server clones from a
recording. The clip is 15 to 28 seconds of one person in one mood:

```sh
media clean recording.wav              # how noisy it is, and what is worth doing about it
media clean recording.wav cleaned.wav  # do that; a recording that is clean enough is left alone
media reference recording.wav          # of a long recording: where it could be cut, the likeliest first
media reference recording.wav voices/ada/neutral.flac --from 12 --to 34 --listen
media voice ada ada --reference voices/ada/neutral.flac --new
media audition ada --out out/audition
```

Without `--from` and `--to`, a recording too long to be a clip is cut at the
first place `media reference` would have listed. Those places are found by
loudness alone: listen that it is one person in one mood.

End the clip in the quiet after a complete sentence (`--to`), and make its
words exactly what is heard: a model goes on from where its clip stops, so a
clip that ends mid-word is heard at the start of every line. `--tighten`
shortens long pauses. On a Mac with no speech server, `pip install mlx-audio`
and `CONSTRUCTIVE_MEDIA_ADAPTER_SPEECH=mlx` run the model in the engine itself.

`media reference` cuts the clip, sets its loudness, writes the words said in it
beside it and says what is wrong with it. `media audition` says a set of lines
and reports any that came back with the clip's own words in them. Only clone a
voice whose owner said yes: your own, or one you hold permission for.

**Make a voice between other voices.** It needs the `mlx` speech adapter
(`CONSTRUCTIVE_MEDIA_ADAPTER_SPEECH=mlx`, or `--engine mlx`), and voices that
were cloned from clips, or recordings:

```sh
media blend ada:0.35 grace:0.65 out/between.wav       # hear it say a passage; shares are in proportion
media blend ada:0.35 grace:0.65 out/between-2.wav --seed 31   # another reading of the same voice
media blend ada:0.35 grace:0.65 voices/wren/neutral.wav --keep wren   # keep it: a clip, its words, a voice for wren
media audition wren
```

It prints how like each voice the blend came out, as the speaker model hears
it (the same person is near 0.7 or over, two people under 0.4). Those numbers
are not the shares, and a blend near the middle is like neither very much:
it is a third voice. One near 0.7 against a source is that source. Make a few with different shares and seeds, give the
person the files, and let them choose: you cannot hear it, and nothing here
says whether it is a voice anyone wants. Keep only the one they chose. A kept
blend is a cloned voice like any other, spoken by anything that clones, and
what it was made of is in the `.blend.json` beside its clip. It carries the
voices it was made from: blend only voices whose owners said yes to that.

**Share voices, sounds and faces as a library.** A library is a folder or a git
repository with a `media-library.json` that names what is in it and, for every
voice, who consented:

```sh
media library check my-library              # is it a library, and what is wrong with it
media library search weather voices         # look in the indexes for a library, by any words
media library add owner/lighthouse-voices   # a GitHub repository, any git address, a folder, or a name an index lists
media library voices lighthouse             # give its voices to the personas here
media library clip ada recording.wav --by "Ada Marsh" --kind own   # in your own library: clean, cut, words, consent, README
media library lock                          # write media-libraries.json beside your scripts
media library install                       # on another machine: the same libraries at the same commits
```

Once added, `@include weather` in a script finds the library's bank, the
bank's sounds are found in it, and its faces are drawn by `media film`. A script
still names only personas and banks, so it renders anywhere the libraries are.
Start a library with `media init library NAME`, from
[media-engine-boilerplates](https://github.com/constructive-io/media-engine-boilerplates),
whose skills `voice-library` and `build-a-voice` go from a recording to a
voice that passes its audition. `media library clip` never guesses `--by`
(whose voice) or `--kind` (`own`, `permission`, `licensed`, `synthetic`): ask
the person, and do not put in a voice whose owner has not said yes.

## Pictures and video from a description

```sh
media generate image "a harbour at dawn, fog over the water"       # 1344x768, about twenty seconds
media generate image "the same harbour at night" --from harbour.png --size 1024x1024
media generate video "fog lifting off the harbour" --seconds 2    # 832x480, minutes: say so before you start
media generations                # every request, how it came out and where the file is
media synthetic talk-over        # the clips of a cut that nobody filmed; status 1 when there are any
```

- **Neither model is installed by the engine.** Pictures need the `mflux`
  programs (`uv tool install mflux`, Apple silicon); video needs
  `pip install torch diffusers transformers ftfy`. Where mflux is not on the
  path: `CONSTRUCTIVE_MEDIA_EXTRA_IMAGE='{"program": "/path/to/mflux-generate-flux2"}'`.
  `media adapters` shows what fills `image` and `video`; all of it is in
  [docs/ADAPTERS.md](../../../docs/ADAPTERS.md#pictures-and-video).
- **The same request is made once.** Run again, the command prints `had` and
  the file. `--again` makes another with a new seed; `--seed N` makes one you
  can make again. `--dry-run` says where it would go and makes nothing.
- **Sides are multiples of 16.** The command says the nearest size that works.
- **One at a time.** A model holds a lock every `media` on the machine shares;
  do not start a second in the background to save time.
- **Look at it.** Read the picture, or pull a frame from the video with
  `ffmpeg -ss 1 -i FILE -frames:v 1 frame.png`, before saying it is right.
  Models draw hands, lettering and faces badly; ask again rather than use one
  that is wrong.
- **It is on record as generated, never as footage.** Do not `media ingest` a
  made file to use it: it is already an asset, and `media lineage FILE` says
  what made it. Before a cut is called a record of what happened, run
  `media synthetic` on it.
- A request handed to a service (an adapter from a plugin) prints `asked`;
  `media collect` brings in what the service made.

## Talk with a model

```sh
media talk --measure         # serve nothing: time hearing, answering and saying, and the wait they add up to
media talk --voice ada       # serve the page on this machine; prints its address and runs until stopped
media talk --typed           # no microphone and no transcription: turns are typed, and answered aloud
```

- **It runs until it is stopped.** Start it in the background, read the
  address it prints (port 8850 unless `--port` says; `--port 0` for a free
  one), give the address to the person, and stop it when they are done. Never
  wait on it in the foreground.
- **Measure first.** `--measure` proves all three models work and says what
  the wait is, with nothing served; status 1 and the reason when one fails.
  Report the wait it prints. Over two seconds, try a smaller transcription
  model or a smaller text model before anything else.
- **It needs the `text` slot**: `CONSTRUCTIVE_MEDIA_URL_TEXT` and
  `CONSTRUCTIVE_MEDIA_MODEL_TEXT`, a server that speaks the usual chat API. A
  model that thinks before it answers spends the wait thinking; a small one
  that does not is right here.
- **You cannot hear it.** You can type a turn at it from a program (a
  WebSocket at `/talk`, described in [docs/TALK.md](../../../docs/TALK.md#from-a-program))
  and read what came back; say that is what you checked, and that nobody
  listened.
- **This machine only.** It listens on `127.0.0.1` and answers only its own
  page. Do not put it behind a proxy or open the port to a network: it is a
  microphone and a model with no login.
- **Nothing is kept.** No turn is written to the database or to a file.

## Start a project

```sh
media init workspace harbour-talks          # a pnpm workspace, with a first deck in packages/
media init deck quarterly-review --title "…"  # at the top of a workspace: another deck
media init walkthrough first-day            # or a tour of an application; `animation` and `document` are the other two
media init --list                           # the templates, and what each asks
```

Every question a template asks is a flag of the same name, and `--no-tty`
takes the defaults. It never writes into a folder that has something in it and
runs nothing a template holds. The templates are in
[media-engine-boilerplates](https://github.com/constructive-io/media-engine-boilerplates),
which has the skills for working in what is made (`media-workspace`,
`narrated-video`, `narrated-document`). `media init` copies the skills a
template names, this one among them, into `.agents/skills/` at the top of the
workspace; `--no-skills` leaves them out. `--repo` reads other boilerplates.
The same templates work with `pgpm init --repo`. More in
[docs/PROJECTS.md](../../../docs/PROJECTS.md).

## Before you say it is done

`media say` ends by saying what it wrote, and then anything that wants a look:

```
3 lines, 9.2 seconds: out/scene.flac
left out, having no file: THUNDER
1 need a listen: 3
```

- **No `need a listen`.** A line named there had no take that passed. In
  `out/scene.manifest.json` that is `needs_review` above 0, and the line's
  `status` is `needs_review`. Say it again with `--only 3`, or reword it.
- **No `left out`.** A sound named there was not in the mix, for want of a
  file: `sound.left_out` in the manifest. Give it a `file:`, or point
  `--sounds` at the folder it is in.
- **Say what was checked.** Whisper listened to every line; no person did.
  Report the render as made and checked by the engine, not as heard.
- **Leave the sound where it is.** Recordings, models and anything under
  `out/` are never committed.

## Writing `.conv`

The grammar is [SPEC.md in conversation-format](https://github.com/constructive-io/conversation-format/blob/main/SPEC.md),
and that repository has a `conv` skill for writing scripts
(`npx skills add https://github.com/constructive-io/conversation-format --skill conv`). The shape of it:

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
  yet. `media say --without-generated` leaves those out instead of stopping.
- **Sound files are yours.** They are looked for beside the script, then in
  each folder given with `--sounds`.

## When something goes wrong

| It says | Do |
| --- | --- |
| `could not connect to database` | Export `CONSTRUCTIVE_MEDIA_DATABASE_URL`, and deploy the two modules into that database |
| `nothing says where the speech model is` | Export `CONSTRUCTIVE_MEDIA_URL_SPEECH` and `CONSTRUCTIVE_MEDIA_MODEL_SPEECH` |
| `` `GRACE` speaks as `grace`, and `grace` has no voice `` | `media voice grace <the server's name for a voice> --new` |
| the speech server refuses the request | It may not take a `seed`. `export CONSTRUCTIVE_MEDIA_EXTRA_SPEECH='{"seed": null}'` |
| a line is marked `needs_review` | No take of it passed. Listen to `out/scene.lines/`, reword the line, or raise `--attempts` |
| `this command needs the '...' package` | Install the extra it names: `pip install ".[all]"` |
| `no Chrome or Chromium was found` | Install one, or export `CONSTRUCTIVE_MEDIA_MODEL_BROWSER` as the program. In a container, `CONSTRUCTIVE_MEDIA_EXTRA_BROWSER='{"flags": ["--no-sandbox"]}'` |
| `this is not a library yet` | `media library check` listed what is missing above that line. A voice with no `consent` is the usual one |

`media status` shows where things are kept; `media adapters` shows every slot and
what fills it; `media <command> --help` describes every command.

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
| `conv` | [conversation-format](https://github.com/constructive-io/conversation-format) | Writing and checking `.conv` scripts |
| `media-engine-plugins` | [media-engine-plugins](https://github.com/constructive-io/media-engine-plugins) | Installing, choosing and writing plugins |
| `pgpm` | [`.agents/skills/pgpm`](../pgpm/SKILL.md) | The database modules: deploy, revert, verify, test |
