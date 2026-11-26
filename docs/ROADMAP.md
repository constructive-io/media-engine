# Roadmap

One engine for video, images, audio and voice. Built in layers; each layer
lands tested, with nothing in it that breaks [the licensing rule](./LICENSING.md).

| # | Layer | State |
| --- | --- | --- |
| 1 | Schema modules: `media-core`, `media-video`, `media-audio` | Done |
| 2 | Settings: one place for the database, paths and model choices, read from the environment | Done |
| 3 | [Adapter interface](./ADAPTERS.md): a registry, plugin discovery, and the first built-in adapter (`ffmpeg` effects) | Done |
| 4 | Engine and `me` command line: ingest, analyse, transcribe, edit, render | Next |
| 5 | Object detection on an Apache-2.0 detector | Planned |
| 6 | Audio: the [`.conv`](https://github.com/constructive-io/conv-spec) renderer, with `ffmpeg` as the default effects backend | Planned |
| 7 | Model list: every supported model, its source, its license | Planned |
| 8 | Plugin repository for adapters that cannot live here | Planned |

## Ground rules for every layer

- No people, projects, voices or footage. Tests use made-up data.
- No machine paths or credentials. Everything local comes from settings.
- No weights and no binaries.
- Each layer passes `pnpm test`, `pgpm test-packages --full-cycle`,
  `pnpm run audit:db` and the Python tests before it merges.
