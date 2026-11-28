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
| 16 | [Plugin repository](https://github.com/constructive-io/media-engine-plugins) for optional adapters, each a package of its own | Done: `pedalboard` effects (GPL-3.0) and Ultralytics detection (AGPL-3.0), each a package of its own |
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
| 27 | Who spoke, from whose microphone heard it: `media wear` says who wore which recording of a take, and `media microphones` gives each line to the wearer of the one that heard it loudest against its own loud level, leaves what is unclear or named by a person alone, and checks each microphone against the voices `media diarize` found | Done. No model |
| 28 | Handing over and keeping house: `media export --preview --renders` makes one folder to send, `media flags` lists what is flagged inside a cut and what cutting it would save, `media outputs` lists every derived file on record and the files in the work folder that nothing accounts for, and `media persona` and `media personas` say who everybody is | Done. No model |
| 29 | Takes by hand: `media take` makes or mends a take and stores an offset a person gives, `media overlap` shows the sentences two recordings share and the offset each implies, and `media pull` cuts one moment from every recording to listen to | Done. No model |
| 30 | What a cut is heard through: `media relink` stores a cut again with its sound moved to the same moment in another recording of its take, and `media render --mix` puts a take's worn microphones under each stretch, mixed to follow whoever is speaking, with the others turned down and never off | Done. No model |
| 31 | Pictures and video from a description: `media generate` asks whatever fills the `image` or the `video` slot, every request is a row with its words, model, settings and seed, the same request is made once, and what is made is on record as `generated` so `media synthetic` can list the clips of a cut that nobody filmed. `media generations` lists the requests and what they came to, and `media collect` brings in what a service made later | Done, and tested with stand-ins for the models. `mflux` (FLUX.2 klein 4B) fills `image` and `diffusers` (Wan 2.1 T2V 1.3B) fills `video`; both are installed by you: see [LICENSING.md](./LICENSING.md) |
| 32 | [Talking with a model](./TALK.md): `media talk` serves a page on your own machine. What is said is found in the microphone's sound (Silero VAD through ONNX Runtime, or loudness with nothing installed), written down by the `transcription` slot, answered by the `text` slot as it is written and said back by the `speech` slot a sentence at a time; speaking over an answer stops it. `media talk --measure` times each model and the wait they add up to | Done, and tried in a real browser with a recording for a microphone. The server and the WebSocket are the standard library's: nothing is added to install |
| 33 | [A voice between other voices](./VOICES.md#a-voice-between-two-voices): `media blend` takes two voices or more as a model that clones hears them, finds the voice that is so much of each on the sphere they lie on, says a passage in it, checks what was said, and says how like each voice it came out. `--keep` cuts the passage as a clip and gives it to a persona, so a blend is a cloned voice like any other from then on | Done, and tried with the `mlx` adapter. The arithmetic is the standard library's |
| 34 | [A quick voice](./VOICES.md#a-quick-voice): the `kokoro` adapter runs a small model that says a sentence in about a tenth of a second, in a voice of its own, and reads a word no dictionary has by its spelling, so nothing under the GPL is needed to say it. `media mimic` hears every one of the model's voices beside a recording, mixes the nearest, and writes the mix as a voice a persona can be given | Done, and tried on a Mac. It is not a clone and says so: over words a mix was not found with, it was no nearer than the nearest of the model's own voices |
| 35 | [A recording said again in another voice](./VOICES.md#a-recording-said-again-in-another-voice): the `voice_conversion` slot has a contract, `conversion.Converter`, for a plugin to fill. `media revoice` says a recording again in a persona's voice or like a clip and prints how like the voice it was before and is after. `media talk --voice WHO --quick NAME` says an answer in a quick voice and again in a cloned one | Done, and tried with the Seed-VC plugin, which is GPL-3.0 and so is in [media-engine-plugins](https://github.com/constructive-io/media-engine-plugins). Nothing built in fills the slot |
| 36 | [A still made to move](./ADAPTERS.md#pictures-and-video): `media generate video "…" --from PICTURE --adapter ltx` starts a piece of video from a picture. The `video` slot's settings belong to the adapter that is chosen, so the engine's own adapter and a plugin keep theirs apart on one machine | Done as a plugin, `ltx`, which runs LTX-Video. Its code is MIT and its model is under Lightricks' own terms, so it is in [media-engine-plugins](https://github.com/constructive-io/media-engine-plugins). Nothing built in starts video from a picture |
| 37 | [A voice taken out from over music](./VOICES.md#a-voice-with-music-under-it): the `separation` slot has a contract, `separation.Separator`, for a plugin to fill. `media separate RECORDING FOLDER` writes the voice and what was under it as a file each, and says how loud each is beside the recording, so a part with nothing in it is seen to have nothing in it | Done, and tried with the Demucs plugin. Its code is MIT and its weights are provided by their authors for research only, so it is in [media-engine-plugins](https://github.com/constructive-io/media-engine-plugins). Nothing built in fills the slot |
| 38 | [A face made to say a recording](./ADAPTERS.md#lip-sync): the `lip_sync` slot has a contract, `lipsync.Syncer`, for a plugin to fill. `media lipsync VIDEO SPEECH OUTPUT` writes the video again with the mouth saying the recording and the recording under it, and says when the video ran out before the speech did | Done, and tried with the LatentSync plugin. Its code is Apache-2.0, its weights say what they may not be used for, and the face models it needs are for non-commercial research only, so it is in [media-engine-plugins](https://github.com/constructive-io/media-engine-plugins). Nothing built in fills the slot |

## Left for later, and why

- **Sounds from a description, and models on a service.** `media generate`
  makes pictures and video from words with what runs on one machine under
  terms within the rule. A sound made from a description needs a slot of its
  own. Larger and closed models, and the services that run them, are adapters
  in [media-engine-plugins](https://github.com/constructive-io/media-engine-plugins);
  the engine already hands a request over and collects it later. Video that
  starts from a picture is there now, as the `ltx` adapter.
- **A denoiser that is a model.** `media clean` uses ffmpeg's own, which is worth
  about ten decibels on a steady hiss. A model does better on noise that
  changes, and is an adapter in the `denoise` slot when one is added.

- **Telling voices apart by pitch.** It was the first way tried and the voice
  model replaced it: two people of similar pitch are one voice to it. The
  `median_f0` column stays for anything that wants to record a pitch.

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
