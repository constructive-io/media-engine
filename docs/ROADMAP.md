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
| 8 | Speakers: the voices in a recording told apart by how they sound (CAM++ through ONNX Runtime), each line given to its voice, and voices given names that carry across recordings | Done |
| 9 | Editing, first part: a take's speech cut into passages, flags on what to cut or keep away from, scores from what was read, heard and seen, and rankings that say how much they had to go on | Done |
| 10 | Editing, the rest: decisions, timelines, shot selection, a judge and search behind the `text` and `embedding` slots, rendering and export | Next |
| 11 | Audio: the [`.conv`](https://github.com/constructive-io/conv-spec) renderer, with `ffmpeg` as the default effects backend; a mix that can also write each track on its own as a stem; and a check on every rendered line, which is listened back to, compared with the words it was meant to say, and made again when it falls short | Planned |
| 12 | Model list: every supported model, its source, its license | Planned |
| 13 | Plugin repository for adapters that cannot live here | Planned |

## Left for later, and why

- **Telling voices apart by pitch.** It was the first way tried and the voice
  model replaced it: two people of similar pitch are one voice to it. The
  `median_f0` column stays for anything that wants to record a pitch.
- **Saying who spoke from whose microphone was loudest.** It needs to know
  who wore which microphone, and it belongs with the audio layer, where the
  microphones of a take are mixed. It comes in with that layer.

- **Scores that need a layer not here yet.** How loud a shot's sound is
  against its neighbours, how well the picture moves with the sound, how
  clean a recording is and where people laugh each wait for the layer that
  measures them. A recipe that weighs one of them says how much of itself it
  could measure, and ranks on the rest.

## Ground rules for every layer

- No people, projects, voices or footage. Tests use made-up data.
- No machine paths or credentials. Everything local comes from settings.
- No weights and no binaries.
- Each layer passes `pnpm test`, `pgpm test-packages --full-cycle`,
  `pnpm run audit:db` and the Python tests before it merges.
- Nothing is given up for a license without measuring it. Where a replacement
  is slower or finds less than what it replaced, that is fixed or written down.
