# constructive-media

<p align="center" width="100%">
  <img height="250" src="https://raw.githubusercontent.com/constructive-io/constructive/refs/heads/main/assets/outline-logo.svg" alt="Constructive" />
</p>

<p align="center" width="100%">
  <a href="https://github.com/constructive-io/constructive-media/actions/workflows/ci.yml">
    <img height="20" src="https://github.com/constructive-io/constructive-media/actions/workflows/ci.yml/badge.svg" alt="CI status" />
  </a>
  <a href="https://github.com/constructive-io/constructive-media/blob/main/LICENSE"><img height="20" src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT license" /></a>
</p>

<p align="center" width="100%">
  One engine for video, images, audio and voice, with Postgres as its memory.
</p>

Constructive Media treats everything it ingests or generates as data. Footage,
stills, recordings and renders are rows; so are the people in them, the shots
and words found inside them, the cuts made from them and every judgement a
person or a model passed along the way. Models come and go behind adapters. The
record of what was made, from what, and why stays in one database.

## Status

Early, and built in the open one layer at a time.

| Layer | State |
| --- | --- |
| Schema modules (`packages/`) | Here, tested, audited |
| Settings and the adapter interface for the database, models and effects (`python/`) | Here, tested |
| Engine and `me` command line: ingest, lineage, shots, frames, signatures, detection, transcription, takes and sync, speakers, passages, flags, scores and rankings, decisions, shot selection and timelines, a ranker taught from preferences, interviews assembled from passages with footage laid over them, cuts rendered to a file and exported to an editing program, a text model's opinion of the best passages, search by meaning | Here, tested |
| Engine: generation | Being brought in |
| [`.conv`](https://github.com/constructive-io/conv-spec) renderer | Being brought in |

The roadmap is in [docs/ROADMAP.md](./docs/ROADMAP.md).

## What is in the box

| Module | Schema | Holds |
| --- | --- | --- |
| [`media-core`](./packages/media-core) | `media` | Personas, voices, assets, takes, episodes and what was derived from what |
| [`media-video`](./packages/media-video) | `media_video` | Shots, frames, transcripts, passages, timelines, scores and editing decisions |
| [`media-audio`](./packages/media-audio) | `media_audio` | Emotions, text models, renders and human review |

`media-core` is the spine: a persona is the same row whether a model is
speaking in their voice or recognising it in a recording. The other modules
depend on it and never on each other.

The Python engine in [`python/constructive_media`](./python/constructive_media)
needs only a database driver; each part of it adds what it uses as an optional
extra. It reaches the database, every model and every effect through an
[adapter](./docs/ADAPTERS.md), so a model is swapped with a setting and a tool
under another license plugs in from outside this repository. Video and audio
are read and written by the `ffmpeg` program.

The engine ships no models, voices, footage or people. You bring your own.

## Installation

```sh
git clone https://github.com/constructive-io/constructive-media
cd constructive-media
pnpm install
```

You also need PostgreSQL 17+ with [pgvector](https://github.com/pgvector/pgvector)
and [pgpm](https://www.npmjs.com/package/pgpm) (`npm install -g pgpm`). See
[Prerequisites](#prerequisites).

## Usage

Deploy the schema into a database of your own:

```sh
# Docker, or skip these two lines and export PGHOST/PGPORT/PGUSER/PGPASSWORD
pgpm docker start
eval "$(pgpm env)"

# media-video and media-audio bring media-core with them
pgpm deploy --database media --createdb --yes --package media-video
pgpm deploy --database media --yes --package media-audio
```

Install the engine with Python 3.11 or later. You also need `ffmpeg` on your
path, and [whisper.cpp](https://github.com/ggml-org/whisper.cpp) for
transcription (`brew install whisper.cpp`):

```sh
pip install ".[video,detection,speakers]"   # or just `pip install .` for the core
export CONSTRUCTIVE_MEDIA_DATABASE_URL=postgres://postgres:password@localhost:5432/media
```

Then point it at some media:

```sh
me ingest ~/footage --project demo   # register every file, once
me shots                             # split each video into shots
me frames                            # save and measure sample frames
me signatures                        # a colour and motion signature per shot
me detect                            # find objects and people in the frames
me vocabulary Zentrova "Ottoline Varga" --project demo   # names to listen for
me transcribe                        # what is said, sentence by sentence, word by word
me group                             # recordings that rolled together become a take
me match                             # a recorder with no clock finds its camera by what was said
me sync                              # place every recording on its take's timeline, and prove it
me takes                             # each take and how much of it is placed
me diarize                           # tell the voices in each recording apart
me speakers                          # each recording's voices, and a clip of each to listen to
me name interview voice1 "Ottoline Varga" --new   # say whose voice it is
me passages                          # cut each take's speech into passages worth using
me score                             # flag what to cut, then score every passage and shot
me rank interview --top 10           # the best of them, and how much each was measured
me judge                             # a text model reads the best of them and says whether anything is said
me index                             # make every passage findable by what it means
me search "the winter the boats came in close"         # the passages that answer to it
me search "why the light went out" --cut dark --seconds 60   # or a cut of them
me script --out script.md            # the best passages as a document to mark keep, cut or trim
me script --read script.md           # store the marks as decisions
me compile reel --count 12           # shots that are good and unalike, stored as a cut
me note reel 3 drop                  # say what you made of a clip
me compile reel-2 --after reel       # the next cut: what was kept stays, what was dropped is replaced
me pairs --render pairs/             # the shots a ranking cannot tell apart, side by side to choose between
me prefer 3f9a01c2 77b0de41          # which of two you would use
me fit --save taste                  # a model of what you prefer, tested on footage held out from it
me recipe reel --like broll --weigh preferred=0.5   # a recipe that ranks by it
me assemble talk --take interview    # the best passages as a cut, each seen and heard through the right recording
me cutaways talk-over --over talk    # other footage over some of it, the sound untouched
me render talk-over                  # the cut as a file to watch
me export talk-over --to handover/   # the cut for an editing program, pointed at the recordings themselves
me lineage out.mp4                   # what a derived file was made from
me status                            # where things are kept and what is held
me adapters                          # every slot and what can fill it
```

Every stage picks up where it left off and only works on what is new. A model
is downloaded the first time a stage needs it: 1.6 GB for the default
transcriber, 29 MB for the default speaker model.

A take is the camera and the microphones that recorded one moment. `me sync`
works out how far apart they started from the words they share, then from how
the sound rises and falls, then from the devices' clocks once those are shown
to agree. Every answer is checked against what is actually said at the same
moment in both, and one that fails the check is not stored. `me sync --check`
runs that check on what is stored and changes nothing.

`me diarize` listens to what was transcribed and finds how many voices there
are and which lines each one spoke. It does not know who anyone is. It calls
them `voice1`, `voice2` and so on and cuts a short clip of each; you listen and
say who it is with `me name`, once, and every line that voice spoke carries the
name. A voice already named in one recording is suggested when it is heard in
another, and never applied without you. An hour and three quarters of talk is
gone over in about fifteen seconds.

`me passages` reads a take as one conversation, however many microphones
heard it, and cuts it where the talking stops. `me score` flags the fillers,
stutters, dead air and lines the recogniser guessed at, then scores each
passage and shot from what was read, heard and seen. A score nobody measured
is left out, never counted as a zero, and `me rank` says how much of its
recipe each result was measured on. A passage in which someone asks for
something not to be used is never ranked, whatever it scores, until a person
dismisses that flag. `me flag` runs the flags on their own.

`me judge` asks what no measurement can: whether anything is said. A passage
of thanks scores as well as an explanation on every measure of form, so the
best-ranked passages, most said in the least time first, are read by a text
model, and what it makes of each is stored as three more scores with the
model's name on them: `substance`, `interest` and `self_contained`. They are
scores like any other, counted by a recipe that weighs them
(`me recipe said --like interview --weigh substance=1`), and a passage is
read once by each model. `me index` keeps every passage as an embedding and
`me search` finds the ones nearest a question in meaning, weighed with how
good each is, so a passage that is on the subject and unusable does not come
first. `--answers` has the text model check that each one answers the
question and is not only about it, and `--cut` stores what was found as a
cut. A passage whose words have been corrected drops out of search until it
is indexed again.

Both models are reached over HTTP, on any server that speaks the
chat-completions and embeddings API: llama.cpp, Ollama, vLLM, or a hosted
one. Nothing is sent until you name the server and the model, so your
transcripts go where you send them and nowhere else, and a passage in which
someone asks for it not to be used is never sent at all:

```sh
export CONSTRUCTIVE_MEDIA_URL_TEXT=http://localhost:11434/v1
export CONSTRUCTIVE_MEDIA_MODEL_TEXT=the-name-the-server-knows-it-by
export CONSTRUCTIVE_MEDIA_URL_EMBEDDING=http://localhost:11434/v1
export CONSTRUCTIVE_MEDIA_MODEL_EMBEDDING=a-model-that-gives-1024-numbers
```

`CONSTRUCTIVE_MEDIA_KEY_TEXT` and `CONSTRUCTIVE_MEDIA_KEY_EMBEDDING` hold a key
for a server that wants one. `CONSTRUCTIVE_MEDIA_EXTRA_TEXT` is a JSON object
sent with every request, for whatever a server calls its own options. A model
that thinks before it answers needs it, either to be told not to
(`{"reasoning_effort": "none"}`, on a server that takes that) or to be allowed
enough for both (`{"max_tokens": 2000}`); `me judge` says so when a model
spends everything it was allowed and answers nothing.

Try a model on a few passages you know before trusting it. One that marks
filler as highly as an explanation adds noise, and small ones are strict to a
fault about what answers a question.

What a person decides is kept, with who decided and when. `me keep` and
`me cut` rule a passage or a shot in or out, everywhere or for one episode,
and `me prefer` records which of two was better. `me script` writes the
best passages out as a plain document in the order they were said; mark
each one and `me script --read` stores the marks, and saying the same thing
twice stores it once.

`me compile` chooses shots for a cut. The best shots by score are the same
shot over and over, so each pick is weighed for its quality less how much
it looks like what is already picked, and the picks are ordered so that
neighbours differ. Every run says how alike its neighbours are beside what
the top of the list would have given. A `--seed` makes a variant, and the
same seed makes the same one. A cut is revised by reacting to its clips with
`me note` and compiling the next one `--after` it. `me timelines` lists the
cuts and `me timeline` shows one.

`me fit` teaches a model from the choices made with `me prefer`: which
measurements of a shot go with being chosen, and by how much. It is tested on
footage it was not fitted to, a whole shoot held out at a time, since shots
from one shoot are alike enough to flatter a model that has seen their
neighbours. The share it gets right is shown beside the half a coin would
get. A model that has not beaten the coin by more than chance is stored with
its figures and left unused. One that has gives every shot a `preferred`
score, and a recipe made with `me recipe` can weigh it beside the rest.

`me assemble` makes an interview into a cut. The passages a person kept go
in first, then the best by a recipe until the cut is as long as it was asked
to be, and every passage passed over is counted with the reason. Each one is
seen through the take's main camera, or another that was rolling, and heard
through a microphone before a camera: the one measured cleanest, or failing
that the one whose words the recogniser was surest of. Quiet at either end
of a passage is trimmed to what the style allows; only the ends move, so a
filler in the middle of a passage stays where it was said. `me styles` lists
the stored manners of cutting, and any one setting can be given on its own.
A cut made `--after` another keeps the clips that were kept or adjusted,
with their sound still in step, and leaves out what was dropped.

`me cutaways` lays other footage over some of a cut's clips while the sound
carries on underneath: the middle of a long clip, or the whole of a short
one, never the first. It leaves out shots with somebody filling the frame
and anything recorded in the takes the cut is made from. A cutaway that was
dropped is replaced by another of the same length.

`me render` makes a cut into a file. Every clip is given a whole number of
frames counted from the start of the cut, so rounding never adds up, and the
sound is cut to the same count in samples: a long cut ends as much in step as
it began. Sound that carries on from one clip to the next in the same
recording is read as one unbroken stretch, with no join to hear under a
cutaway; where it does jump, it fades out and in over a fiftieth of a second.
Each stretch is measured and brought to one loudness, with one gain for the
whole of it and a limiter under a ceiling, or left `--as-recorded`. The
picture is padded to the canvas or cropped to fill it, and a crop moves to
where the people in the shot were found. It reads the small copies made at
ingest unless `--originals` is given, and `--megabytes` holds the file to a
size. When it is done it looks at frames through the file and says so if
they are not all the size that was asked for, and the file is recorded with
the recordings it was made from, so `me lineage` can answer for it.

`me export` hands a cut to an editing program. It writes FCPXML, which Final
Cut, Resolve and Premiere import as a timeline, the same cut as a CMX3600
edit decision list for anything else, and a page that says what both point
at. Nothing is copied: the files name the original recordings where they are
kept. Times are exact fractions of a second at the rate the footage was shot
at, so 29.97 is 30000 frames in 1001 seconds and not a rounding of it, and
each is given on the recording's own clock where it has one: the timecode a
camera wrote for its first frame, drop-frame or not, and the time after
midnight a sound recorder stored. A clip heard through another recording has
that sound under it and the camera's own switched off. A cut whose footage
was shot at more than one rate says so, and so does one that points at a
recording that is not there.

The same engine from Python:

```python
from constructive_media import effects

effects.apply("line.wav", "line-wet.wav", effects.Effects(reverb="hall", gain=-3))
```

Settings are environment variables that start with `CONSTRUCTIVE_MEDIA_`; they
are listed in [`settings.py`](./python/constructive_media/settings.py).

Run the tests and the checks CI runs:

```sh
pnpm test                          # each module, in a throwaway database
pgpm test-packages --full-cycle    # deploy, verify, revert, deploy again
pnpm run audit:db                  # schema audit (security and performance)
PYTHONPATH=python python -m unittest discover -s tests -t .
```

The Python tests make their own media with `ffmpeg` and skip what your machine
cannot run. Those that touch the database run when
`CONSTRUCTIVE_MEDIA_TEST_DATABASE_URL` names a database with the modules
deployed, and roll back everything they do.

## Licensing

Everything in this repository is MIT licensed, and it stays that way:

- Code here is MIT. Dependencies are MIT, Apache-2.0 or BSD, and a test fails
  the build on one that is not.
- No copyleft code or dependency (GPL, AGPL, LGPL, MPL) is added here. Adapters
  for tools under those licenses live in a separate plugin repository, under
  their own license, and the engine works without any of them.
- `ffmpeg`, whisper.cpp and PostgreSQL are programs the engine runs, not code
  it contains. They keep their own licenses and you install them yourself.
- No model weights, voices, audio or footage are committed. A model's license
  applies to whoever downloads and runs it.

The full rule is in [docs/LICENSING.md](./docs/LICENSING.md).

## Related

- [conv-spec](https://github.com/constructive-io/conv-spec): the `.conv`
  screenplay format for multi-voice audio. This engine is a renderer for it.
- [pgpm](https://www.npmjs.com/package/pgpm): the Postgres package manager the
  schema modules are built with.

## Developing

### Auditing

[safegres](https://www.npmjs.com/package/safegres) grades the schema this workspace
deploys on two independent axes — security (grants, RLS, policy coverage and behavior)
and performance (predicates no index can serve, per-row function calls, foreign keys
without a covering index). It runs from the workspace root and needs no configuration
per module: every module under `packages/` is deployed into one ephemeral database and
that catalog is scanned, so a module joins the audit the moment `pgpm init` creates it.

```sh
# Audit: deploys every module into an ephemeral database and scans its catalog
pnpm run audit:db

# Accept today's performance findings as debt, so CI gates only on new ones
pnpm run audit:db:baseline

# Audit a database you already have instead
pnpm exec safegres audit --database your_db
```

The gates and the exposed surface to grade against live in `safegres.config.js`;
the run also writes JSON, markdown and SARIF reports to `safegres-reports/`.
`safegres-perf-baseline.json` is committed — it is the accepted performance debt
(today, only unscored notes about unindexed timestamp columns), and the only audit
file that carries state. CI runs the same audit
as a single `Database audit (safegres)` job.

Declaring `exposure` in the config is the first thing worth doing: until the audit
knows which schemas and roles your API reaches it has to assume the whole database
is, and caps the score at the `B` the gate allows.

### Using your own PostgreSQL

`pgpm docker start` is a convenience, not a requirement. If PostgreSQL (17+) is already
running on your machine, point pgpm at it and carry on — nothing else changes:

```sh
export PGHOST=localhost PGPORT=5432 PGUSER=postgres PGPASSWORD=yourpassword
pgpm admin-users bootstrap --yes   # once: creates the roles pgpm and tests expect
```

Don't run `eval "$(pgpm env)"` in that case: it prints the Docker container's defaults
(`postgres`/`password` on `localhost:5432`) and would overwrite yours. If you want the
Docker container *alongside* an existing server, give it another port and match it:
`pgpm docker start --port 5433` then `export PGPORT=5433`.

### Install scripts

`pnpm install` never asks you to approve build scripts here: every dependency that
wants to run code at install time already has a decision in `pnpm-policy.yaml`. If a
new dependency stops the install with `ERR_PNPM_IGNORED_BUILDS`, add it there — under
`allowBuilds` with a reason if it truly needs its script, or as `false` if it does not —
and run `pnpm run policy`. Don't use `pnpm approve-builds`; it writes outside the
managed block and `pnpm run policy:check` will fail.

### Working with an AI agent

`pgpm init workspace` installed the `pgpm` skill at `.agents/skills/pgpm/` — a guide to
pgpm's commands, the deploy/revert/verify pattern, plan files, dependencies, testing, and
CI. Agents that read `.agents/skills/` (Codex, Cursor, Gemini CLI, opencode, ...) pick it
up automatically; see [AGENTS.md](./AGENTS.md).

For an agent that looks elsewhere, either point it
at `.agents/skills/pgpm/SKILL.md` directly, or install the skill into the location it
expects:

```sh
npx skills add https://github.com/constructive-io/constructive --skill pgpm
```

### Prerequisites

- Node.js 22+
- pnpm
- Docker, or a local PostgreSQL 17+ you have superuser credentials for
- PostgreSQL client tools (`psql`)
- pgpm (`npm install -g pgpm`)

See [Prerequisites](https://constructive.io/learn/quickstart/prerequisites) for detailed setup instructions.

## Credits

**🛠 Built by the [Constructive](https://constructive.io) team — creators of modular Postgres tooling for secure, composable backends. If you like our work, contribute on [GitHub](https://github.com/constructive-io).**

## Disclaimer

AS DESCRIBED IN THE LICENSES, THE SOFTWARE IS PROVIDED "AS IS", AT YOUR OWN RISK, AND WITHOUT WARRANTIES OF ANY KIND.

No developer or entity involved in creating this software will be liable for any claims or damages whatsoever associated with your use, inability to use, or your interaction with other users of the code, including any direct, indirect, incidental, special, exemplary, punitive or consequential damages, or loss of profits, cryptocurrencies, tokens, or anything else of value.
