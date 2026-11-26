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
| 10 | Editing, second part: decisions that are kept with who made them, a script of the best passages that is marked up and read back, shots selected to be good and unalike, and timelines that are revised from what was said about them | Done |
| 11 | Editing, third part: a ranker that learns from preferences, tested on footage held out from it | Done |
| 12 | Editing, fourth part: interviews assembled from passages, each seen through a camera that was rolling and heard through the microphone that heard it best, with other footage laid over them | Done |
| 13 | Editing, the rest: cuts rendered to a file and exported to an editing program; a text model's opinion of the best passages and search by meaning, behind the `text` and `embedding` slots, through any server that speaks the usual API | Done |
| 14 | Audio: the [`.conv`](https://github.com/constructive-io/conv-spec) renderer, with `ffmpeg` as the default effects backend; a mix that can also write each track on its own as a stem; and a check on every rendered line, which is listened back to, compared with the words it was meant to say, and made again when it falls short | Here: scripts are read and checked, held to the format's conformance files, and rendered with `me say`, every line said and checked until a take passes, the mix written with its stems, and the lines that fell short queued for a listen. Tested with stand-ins for the speech and listening models; a sound described by a `prompt:` waits for a slot that makes sounds |
| 15 | Model list: every supported model, its source, its license | Planned |
| 16 | Plugin repository for adapters that cannot live here | Planned |

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
- **Cutting fillers out of the middle of a passage.** A stored style can ask
  for it and assembly does not do it yet: it trims quiet from a passage's
  ends and nothing inside. A cut inside a sentence has to be heard to be
  trusted, so it waits for rendering.
- **Opening an export in an editing program.** The exported files are tested
  against the formats as they are written down. They have not yet been
  imported into Final Cut, Resolve or Premiere, and the first import into
  each is the test that counts.

- **Embeddings of another size.** The library keeps 1024 numbers for a
  passage, and an embedding model that gives another count is refused. A
  second size is a second column and a second index, and comes when a model
  worth having needs it.
- **Searching pictures.** The `embedding` slot is for text and images; only
  text is searched so far.

## Ground rules for every layer

- No people, projects, voices or footage. Tests use made-up data.
- No machine paths or credentials. Everything local comes from settings.
- No weights and no binaries.
- Each layer passes `pnpm test`, `pgpm test-packages --full-cycle`,
  `pnpm run audit:db` and the Python tests before it merges.
- Nothing is given up for a license without measuring it. Where a replacement
  is slower or finds less than what it replaced, that is fixed or written down.
