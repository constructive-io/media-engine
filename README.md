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
| Engine and `me` command line (ingest, analyse, edit, generate) | Being brought in |
| Adapter interface for models and effects | Being brought in |
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

Run the tests and the checks CI runs:

```sh
pnpm test                          # each module, in a throwaway database
pgpm test-packages --full-cycle    # deploy, verify, revert, deploy again
pnpm run audit:db                  # schema audit (security and performance)
```

## Licensing

Everything in this repository is MIT licensed, and it stays that way:

- Code here is MIT. Dependencies are MIT or Apache-2.0.
- No copyleft code or dependency (GPL, AGPL, LGPL) is added here. Adapters for
  tools under those licenses live in a separate plugin repository, under their
  own license, and the engine works without any of them.
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
