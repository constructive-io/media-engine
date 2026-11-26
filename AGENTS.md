# constructive-media — agent guide

This is a [pgpm](https://www.npmjs.com/package/pgpm) workspace: PostgreSQL schemas are
developed as versioned modules under `packages/`, each with a `pgpm.plan` and matching
`deploy/`, `revert/`, and `verify/` SQL for every change.

## What this repository is

Constructive Media: one engine for video, images, audio and voice, with its state in
PostgreSQL. `packages/` holds the schema modules (`media-core`, `media-video`,
`media-audio`). See [README.md](./README.md) and [docs/ROADMAP.md](./docs/ROADMAP.md).

## Rules that are specific to this repository

- **MIT or Apache-2.0 only.** Read [docs/LICENSING.md](./docs/LICENSING.md) before adding
  a dependency or copying code in. No GPL, AGPL or LGPL, no non-commercial code. Adapters
  for such tools go in the separate plugin repository, never here.
- **No content.** No model weights, voices, audio, images or footage, and no binaries.
- **Nothing personal or local.** No real people, project names, machine paths or
  credentials in code, comments, tests, docs or commit messages. Tests use made-up data.
- **Modules depend on `media-core`, never on each other.**
- The Python engine (`python/constructive_media`) has no dependencies. A model or
  library is reached through an adapter (`docs/ADAPTERS.md`) and imported only
  inside that adapter's factory. Tests are standard-library `unittest`:
  `PYTHONPATH=python python -m unittest discover -s tests -t .`

## The pgpm skill

`pgpm init workspace` installed the `pgpm` skill into this repo:

```
.agents/skills/pgpm/
├── SKILL.md          # start here — concepts, commands, and a reference index
└── references/       # cli, changes, plan format, dependencies, extensions,
                      # docker, env, testing, publishing, ci-cd, troubleshooting, ...
```

Read `.agents/skills/pgpm/SKILL.md` before writing SQL or running migrations, and follow
its links into `references/` for the task at hand. It is the source of truth for this
workspace's conventions — prefer it over guessing pgpm commands or file layouts.

If your agent does not discover `.agents/skills/` on its own, point it at
`.agents/skills/pgpm/SKILL.md` directly, or install the skill wherever your tool expects
it:

```sh
npx skills add https://github.com/constructive-io/constructive --skill pgpm
```

## Ground rules

- Every change is a triple: `deploy/<change>.sql`, `revert/<change>.sql`, `verify/<change>.sql`.
- Never hand-edit generated artifacts under `packages/*/sql/` — run `pgpm package`.
- Deploy and revert against a local database, never against anything shared.
- Before starting a database, check for one: if `pg_isready -h localhost -p 5432` already
  answers, the developer runs their own PostgreSQL — use it with the credentials they give
  you (`PGHOST`/`PGPORT`/`PGUSER`/`PGPASSWORD`), and do not stop it or start Docker on top
  of it. Only when nothing is listening: `pgpm docker start`, then `eval "$(pgpm env)"`.
  `pgpm env` prints the Docker defaults, so never run it against a developer's own server.
- The database audit is workspace-level: `pnpm run audit:db` at the root deploys every
  module into an ephemeral database and grades it (`safegres.config.js`). Do not add
  per-module safegres configs or scripts — declare `exposure` and retune gates in the
  root config, and re-run `pnpm run audit:db:baseline` (and commit the result) only when
  deliberately accepting performance findings as debt.
- Never run `pnpm approve-builds`. Install-script decisions live in `pnpm-policy.yaml`;
  edit that and run `pnpm run policy` (see README, "Install scripts").
