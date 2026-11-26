# Roadmap

One engine for video, images, audio and voice. Built in layers; each layer
lands tested, with nothing in it that breaks [the licensing rule](./LICENSING.md).

| # | Layer | State |
| --- | --- | --- |
| 1 | Schema modules: `media-core`, `media-video`, `media-audio` | Done |
| 2 | Settings: one place for the database, paths and model choices, read from the environment | Done |
| 3 | [Adapter interface](./ADAPTERS.md): a registry, plugin discovery, and the first built-in adapter (`ffmpeg` effects) | Done |
| 4 | Engine and `me` command line, first part: the database driver, the `ffmpeg` bridge, ingest, lineage, shots, frames and colour signatures | Done |
| 5 | Object detection on an Apache-2.0 detector (D-FINE through ONNX Runtime), with torchvision as a second choice | Done |
| 6 | Transcription: speech to sentences and timed words with Whisper through whisper.cpp, and a vocabulary to listen for | Done |
| 7 | Takes and sync: recordings of one moment grouped by clock or matched by content, then placed on one timeline by shared words, by sound and by clock, each placing proved against what is said | Done |
| 8 | Engine, the rest: speakers, editing, rendering and export | Next |
| 9 | Audio: the [`.conv`](https://github.com/constructive-io/conv-spec) renderer, with `ffmpeg` as the default effects backend | Planned |
| 10 | Model list: every supported model, its source, its license | Planned |
| 11 | Plugin repository for adapters that cannot live here | Planned |

## Ground rules for every layer

- No people, projects, voices or footage. Tests use made-up data.
- No machine paths or credentials. Everything local comes from settings.
- No weights and no binaries.
- Each layer passes `pnpm test`, `pgpm test-packages --full-cycle`,
  `pnpm run audit:db` and the Python tests before it merges.
- Nothing is given up for a license without measuring it. Where a replacement
  is slower or finds less than what it replaced, that is fixed or written down.
