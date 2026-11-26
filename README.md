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
| Engine and `me` command line: ingest, lineage, shots, frames, signatures, detection, transcription, takes and sync, speakers, passages, flags, scores and rankings, decisions, shot selection and timelines | Here, tested |
| Engine: rendering, generation | Being brought in |
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
me script --out script.md            # the best passages as a document to mark keep, cut or trim
me script --read script.md           # store the marks as decisions
me compile reel --count 12           # shots that are good and unalike, stored as a cut
me note reel 3 drop                  # say what you made of a clip
me compile reel-2 --after reel       # the next cut: what was kept stays, what was dropped is replaced
me pairs                             # the shots a ranking cannot tell apart, to choose between
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
