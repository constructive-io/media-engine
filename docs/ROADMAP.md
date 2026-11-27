# Roadmap

One engine for video, images, audio and voice. Built in layers; each layer
lands tested, with nothing in it that breaks [the licensing rule](./LICENSING.md).

| # | Layer | State |
| --- | --- | --- |
| 1 | Schema modules: `media-core`, `media-video`, `media-audio` | Done |
| 2 | Settings: one place for the database, paths and model choices, read from the environment | Done |
| 3 | [Adapter interface](./ADAPTERS.md): a registry, plugin discovery, and the first built-in adapter (`ffmpeg` effects) | Done |
| 4 | Engine and `media` command line, first part: the database driver, the `ffmpeg` bridge, ingest, lineage, shots, frames and colour signatures | Done |
| 5 | Object detection on an Apache-2.0 detector (D-FINE through ONNX Runtime), with torchvision as a second choice | Done |
| 6 | Transcription: speech to sentences and timed words with Whisper through whisper.cpp, and a vocabulary to listen for | Done |
| 7 | Takes and sync: recordings of one moment grouped by clock or matched by content, then placed on one timeline by shared words, by sound and by clock, each placing proved against what is said | Done |
| 8 | Speakers: the voices in a recording told apart by how they sound (CAM++ through ONNX Runtime), each line given to its voice, and voices given names that carry across recordings | Done |
| 9 | Editing, first part: a take's speech cut into passages, flags on what to cut or keep away from, scores from what was read, heard and seen, and rankings that say how much they had to go on | Done |
| 10 | Editing, second part: decisions that are kept with who made them, a script of the best passages that is marked up and read back, shots selected to be good and unalike, and timelines that are revised from what was said about them | Done |
| 11 | Editing, third part: a ranker that learns from preferences, tested on footage held out from it | Done |
| 12 | Editing, fourth part: interviews assembled from passages, each seen through a camera that was rolling and heard through the microphone that heard it best, with other footage laid over them | Done |
| 13 | Editing, the rest: cuts rendered to a file and exported to an editing program; a text model's opinion of the best passages and search by meaning, behind the `text` and `embedding` slots, through any server that speaks the usual API | Done |
| 14 | Audio: the [`.conv`](https://github.com/constructive-io/conversation-format) renderer, with `ffmpeg` as the default effects backend; a mix that can also write each track on its own as a stem; and a check on every rendered line, which is listened back to, compared with the words it was meant to say, and made again when it falls short | Done, and tested with stand-ins for the models that speak and listen. A sound described by a `prompt:` waits for a slot that makes sounds |
| 15 | [Model list](./MODELS.md): every supported model, its source, its license | Done |
| 16 | [Plugin repository](https://github.com/constructive-io/media-engine-plugins) for adapters that cannot live here | Done: `pedalboard` effects (GPL-3.0) and Ultralytics detection (AGPL-3.0), each a package of its own |
| 17 | [The stage](./STAGE.md): a rendered script filmed as a video. A timeline of what fell when, a player any page loads, layouts for a deck, a document and a walkthrough, a browser behind the `browser` slot, and clips put in with their own frames | Done, and filmed with a real browser in CI |
| 18 | [Voices](./VOICES.md): a recording measured and cut into a clip to clone from, the clip sent to any server that clones, one line said in one voice, and a voice auditioned for its words and for its clip leaking in | Done, and tested with stand-ins for the models |
| 19 | [Libraries](./LIBRARIES.md): voices, banks and faces in repositories of their own, with consent asked of every voice; added by address, kept at a commit, and written down for a project so another machine has the same | Done |
| 20 | [Indexes](./LIBRARIES.md#find-one): a list of libraries in a repository, read by name and by what they hold, so that a library is found with `media library search` and added with `media library add NAME`; it says where a library is and vouches for nothing | Done |
| 21 | [Cleaning a recording](./VOICES.md#clean-a-recording-first): the noise under a voice measured in the quiet between phrases, the least that will do advised and done (a high-pass, notches at a hum, a denoiser behind the `denoise` slot), and the same stretches measured again to say what was gained and what the voice lost | Done, with ffmpeg's denoiser |
| 22 | Speech on a Mac with no server: the `mlx` speech adapter runs a model that clones (Qwen3-TTS) in the engine's own process, one at a time across a machine | Done, and tested with a stand-in for the model. `mlx-audio` is installed by you: see [LICENSING.md](./LICENSING.md) |
| 23 | Movement on the stage: [how a thing arrives](./STAGE.md#how-a-thing-arrives) (`data-enter`), a page that draws by the script's clock (`stage.since`), and who is speaking written on the page | Done, and filmed with a real browser |
| 24 | [Starting a project](./PROJECTS.md): `media init` reads a repository of boilerplates in the format `pgpm init` reads, with the standard library alone, and makes a pnpm workspace and the decks in it. The templates are in [media-engine-boilerplates](https://github.com/constructive-io/media-engine-boilerplates), and both tools write the same files from them | Done |
| 25 | Measures of the sound and the movement: how loud each shot is against the minute around it, how well each transcribed line can be heard, where people laugh (candidates a person confirms), and whether the lit part of a shot moves with the rest of it; each becomes a score a recipe can weigh, and the cleanest recording measured is the one a cut is heard through | Done. No model: each is arithmetic on the sound or the pictures |
| 26 | Keeping transcripts right: a term carries what it is misheard as and `media mend` rewrites those in the lines and their words, with what was heard kept beside each and every change recorded, so all of it can be put back; `media relisten` transcribes the weakest recordings again and replaces a transcript only where the new one is surer and found no less | Done. No model of its own: mending is a rewrite, and listening again uses the `transcription` slot |

## Left for later, and why

- **Blending two voices, and taking a voice out of a recording with music.**
  Each is a model of its own behind a slot of its own. The `mlx` speech adapter
  can already hand over what a blend is made of (`embed` and `say_as`); the
  command that blends, scores and keeps one is not written. A clip is made
  from a recording of one voice until then.
- **A denoiser that is a model.** `media clean` uses ffmpeg's own, which is worth
  about ten decibels on a steady hiss. A model does better on noise that
  changes, and is an adapter in the `denoise` slot when one is added.

- **Telling voices apart by pitch.** It was the first way tried and the voice
  model replaced it: two people of similar pitch are one voice to it. The
  `median_f0` column stays for anything that wants to record a pitch.
- **Saying who spoke from whose microphone was loudest.** It needs to know
  who wore which microphone, and it belongs with the audio layer, where the
  microphones of a take are mixed. It comes in with that layer.

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
