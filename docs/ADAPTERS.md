# Adapters

The engine never imports a database driver, a model library or an effects
library by name. It asks for whatever fills a **slot** and is handed an object
that does the job.

- Swapping a model is a setting, not a code change.
- Built-in adapters are MIT, Apache-2.0 or BSD, and so is everything they
  import. The registry refuses to register a built-in under any other license.
- A tool under another license is still usable. Its adapter lives in a separate
  package, under its own license, and the engine finds it at run time.

## Slots

| Slot | Fills | Built in |
| --- | --- | --- |
| `database` | the PostgreSQL driver | `pg8000` |
| `transcription` | speech to text, with word timings | `whispercpp` |
| `detection` | objects and people in a frame | `onnx`, `torchvision` |
| `speaker` | a voice as numbers, alike for one person and apart for two | `onnx` |
| `speech` | text to speech | `http`, `mlx` |
| `denoise` | a voice with the noise under it taken off | `ffmpeg` |
| `voice_conversion` | one voice re-spoken in another | |
| `image` | a picture made from a description | `mflux` |
| `video` | a piece of video made from a description | `diffusers` |
| `lip_sync` | a face re-timed to new speech | |
| `effects` | audio effects | `ffmpeg` |
| `text` | text generation and judgement | `http` |
| `embedding` | text and image embeddings | `http` |
| `browser` | a page opened, watched and printed | `chrome` |

Slots fill in as the engine lands; see the [roadmap](./ROADMAP.md). `media adapters`
lists what is installed on your machine, built in and plugged in.

## Using one

```python
from constructive_media import adapters, effects

backend = adapters.get("effects")            # the default, or the one settings choose
backend = adapters.get("effects", "ffmpeg")  # by name

effects.apply("line.wav", "line-wet.wav", effects.Effects(reverb="hall", gain=-3))
```

Which adapter fills a slot is decided, in order, by the name passed in, then
`CONSTRUCTIVE_MEDIA_ADAPTER_<SLOT>`, then the slot's default:

```sh
export CONSTRUCTIVE_MEDIA_ADAPTER_EFFECTS=ffmpeg
```

Where an adapter can load more than one model, `CONSTRUCTIVE_MEDIA_MODEL_<SLOT>`
chooses it:

```sh
export CONSTRUCTIVE_MEDIA_MODEL_DETECTION=dfine_s
```

An adapter's libraries are imported the first time it is used, never before.

## Writing a plugin

A plugin is an ordinary Python package. It declares an entry point:

```toml
[project.entry-points."constructive_media.adapters"]
my_plugin = "my_plugin:adapters"
```

and that entry point returns its adapters:

```python
from constructive_media.adapters import Adapter, lazy

def adapters():
    return [
        Adapter(
            slot="effects",
            name="my-effects",
            factory=lazy("my_plugin.effects:MyEffects"),
            license="GPL-3.0-only",   # the SPDX identifier, stated honestly
            summary="What it does, in a line",
        )
    ]
```

Install the package next to `constructive-media` and the adapter is available
by name. A plugin cannot replace a built-in adapter, and one that fails to load
is skipped with a warning rather than stopping the engine.

What an adapter must do is set by its slot:

| Slot | Interface | In short |
| --- | --- | --- |
| `database` | `constructive_media.db.Driver` | `connect(database)` returns a connection whose cursors take `%s` parameters and return tuples |
| `transcription` | `constructive_media.transcription.Transcriber` | `transcribe(audio, language, prompt)` returns what was said as segments, with a start and an end for every word |
| `detection` | `constructive_media.detection.Detector` | `detect(images, confidence)` returns the detections for each image, boxes as shares of the frame |
| `speaker` | `constructive_media.speakers.Embedder` | `embed(sounds)` returns one row of numbers for each stretch of sound, at unit length, close together for one voice |
| `effects` | `constructive_media.effects.EffectBackend` | `apply(source, target, effects)` writes the audio with the effects applied, and answers a WAV file of floats with one, so a track that passes full scale on its way into a mix is not clipped. It may also say how long its effects ring, with `ring(effects)`, and a mix leaves that much room |
| `image`, `video` | `constructive_media.generate.Maker` | `make(request, target)` writes the picture or the piece of video a request describes to `target`. Its `name` and `model` go on record beside what it made. It may say what a request costs, with `price(request)`. One that asks a service and is answered later has `submit(request)`, `poll(job)` and `fetch(job, target)` in place of `make` (`constructive_media.generate.Later`), and `media collect` brings in what it made |

## Transcription

`whispercpp` runs OpenAI's Whisper through
[whisper.cpp](https://github.com/ggml-org/whisper.cpp). whisper.cpp is a
program you install (`brew install whisper.cpp`, or build it); the engine runs
its `whisper-cli` and `whisper-vad-speech-segments` and needs no Python package
for it. The model you choose is downloaded on first use and checked against a
pinned hash. `CONSTRUCTIVE_MEDIA_MODEL_TRANSCRIPTION` takes a name the adapter
knows (`large-v3-turbo`, `large-v3`, `medium`, `small`, `base`, `tiny`, and
their English-only and smaller-file variants) or the path of any model file in
whisper.cpp's format.

The adapter does five things on purpose, each because the plain way measured
worse:

- **Only speech is heard.** The stretches where nobody speaks are found first
  and left out. Given a whole noisy recording, the same model wrote two fifths
  more words, the extra ones over noise.
- **Speech is handed over in pieces that fit Whisper's thirty-second window and
  end on a pause.** A word that straddles the edge of a window is a word lost.
  A stretch with no pause in it is left whole, never cut mid-sentence.
- **Nothing already written is fed back in**, which is what sends Whisper into
  repeating one line for minutes.
- **Word times come from the model's attention** rather than its own timestamp
  guesses. It costs about a fifth more time.
- **A vocabulary is shown where it is safe.** `media vocabulary` holds the names
  and terms no general model will guess, and they are shown to the model
  before it listens. That costs something: shown a vocabulary, Whisper stops
  marking time as it writes. So only pieces that fit one window are shown it,
  and the two smallest model families never are.

Measured on one Apple silicon desktop, model already downloaded. The scripted
recording is 6.6 minutes and 1,051 words of synthetic speech read from a known
script; the other two are real recordings of people talking, with nothing to
check the words against:

| Model | Scripted speech, 6.6 min | Dense talk, 6.9 min | Sparse, noisy talk, 5.5 min |
| --- | --- | --- | --- |
| `large-v3-turbo` (default) | 9.0 s, 1.1% of words wrong | 12.3 s | 5.8 s |
| `base` | 5.3 s, 1.0% of words wrong | 6.7 s | 4.4 s |
| `base`, whole file at once | 5.1 s, 3.1% of words wrong | 5.4 s | 4.3 s |

On clean scripted speech the small model keeps up. On real talk it does not:
`base` wrote down 81% of the words `large-v3-turbo` found in the dense
recording and 70% in the noisy one. The Python `openai-whisper` package with
its `base` model, run the usual way on the same two recordings, took 10.1 s and
14.4 s and found 82% and 73%.

What a vocabulary does, with `large-v3-turbo`: on the scripted recording,
wrong words fell from 12 to 5, and in a shorter script built around two made-up
names, those names came out right 8 times of 8 instead of 2. On the dense
recording it changed about one word in a hundred. With `base` it lost or
changed a fifth of the words in the noisy recording, which is why `base` and
`tiny` are never shown one. `media transcribe --no-vocabulary` leaves it out.

One thing is not fixed. A piece heard with nothing before it is sometimes
written without capitals or punctuation: 14 of the dense recording's 159
segments, 9 of 127 with a vocabulary shown. The words and their times are
unaffected, and the engine breaks such a run at its longest pauses instead of
at sentences.

Whatever the adapter, the engine tidies what comes back before storing it:
segments are cut into sentences, captions for sounds (`[MUSIC]`) are dropped,
so is the vocabulary read back as if it were speech, and so is a line repeated
over and over.

## Detection

Two detectors are built in.

`onnx`, the default, runs transformer detectors through ONNX Runtime. It knows
two [D-FINE](https://github.com/Peterande/D-FINE) models by name and downloads
the one you choose on first use, checked against a pinned hash. It also runs
any model exported in the same shape: point `CONSTRUCTIVE_MEDIA_MODEL_DETECTION`
at a folder holding `model.onnx` and its `config.json`.

`torchvision` runs any detection model torchvision can build by name. Use it
when PyTorch is already part of your setup.

Measured on 60 frames on one Apple silicon desktop, reading the files included.
Accuracy is the COCO box AP each model's authors publish, not something
measured here:

| Adapter | Model | Frames a second | Published AP |
| --- | --- | --- | --- |
| `onnx` | `dfine_n` (default) | 48 on the CPU | 42.8 |
| `onnx` | `dfine_s` | 27 on the CPU | 48.5 |
| `torchvision` | `fasterrcnn_resnet50_fpn_v2` (default) | 9 on the GPU, 1 on the CPU | 46.7 |
| `torchvision` | `fasterrcnn_mobilenet_v3_large_320_fpn` | 65 on the GPU, 9 on the CPU | 22.8 |

## Speakers

`onnx` runs speaker models through ONNX Runtime. It knows three
[WeSpeaker](https://github.com/wenet-e2e/wespeaker) models by name and
downloads the one you choose on first use, checked against a pinned hash. It
also runs any model of the same shape, one that takes 80-band filterbank
frames and gives one row per sound: point `CONSTRUCTIVE_MEDIA_MODEL_SPEAKER` at
a folder holding `model.onnx`.

Measured on the CPU of one Apple silicon desktop, on windows of a second and a
half:

| Model | File | Speed |
| --- | --- | --- |
| `campplus` (default) | 29 MB | 420 times real time |
| `resnet34` | 27 MB | 165 times |
| `resnet221_lm` | 95 MB | 36 times |

The adapter only turns sound into numbers. What the engine does with them is
the same whatever fills the slot:

- **It listens where the transcript says someone spoke**, a second and a half
  at a time, and groups what it hears. A line goes to the voice most of it
  sounds like.
- **Nobody has to say how many people there are.** A group is split while its
  two halves sound like different people. `media diarize --voices 3` says it
  outright when you know.
- **A short "yes" is given to a voice only when it is clearly that voice**, and
  is otherwise left unknown. A wrong name on a line is worse than none.
- **When it is unsure whether there are two people or one, it says two.** Two
  voices that turn out to be one person are put right by giving both the same
  name. Two people merged into one voice cannot be put right without going
  over the recording again.
- **Names are yours to give.** A voice is `voice1` until `media name` says who it
  is. Going over a recording again keeps its names, and a voice named in one
  recording is suggested in the next, as a suggestion only.

Measured on real recordings of people talking, checked against voices that had
been named by hand:

- One recording, an hour and three quarters: gone over in 15 seconds,
  decoding included. Of what each voice was given, 99.3% belonged to one
  person. One line in fourteen was left unknown, most of them too short to
  try, under 0.4 seconds.
- ECAPA through PyTorch, which this replaced, took 379 seconds on that
  recording for the model alone where CAM++ takes 8, and agreed with the named voices less often: 94.2%
  against 99.1%.
- Five recordings of one event, on good microphones and poor ones: no two
  people were merged into one voice in any of them, and between 93% and 100% of
  each voice's time belonged to one person. Where 9, 5, 4, 4 and 5 people could
  be told apart by hand, it found 12, 6, 5, 6 and 5 voices: someone who speaks
  at length is now and then found twice.
- That last default was learned the hard way. Set by one recording on a good
  microphone, the bar for "two people" merged different people on three of the
  other four. On a poor microphone the room is in every voice, and two people
  measured as alike as 0.64 where on a good one they measured 0.47 at most.
  `--alike` moves the bar.
- Pieces shorter than a window, cut from the middle of longer speech so the
  answer was known: at 0.4 seconds, 77% were clear enough to place and 97.6% of
  those were placed right; at 0.8 seconds, 95% and 99.6%. A real "yes" is
  harder than a piece cut from a sentence.
- One person on two microphones measured 0.55 and up; two different people,
  0.47 at most. That gap is why a name is suggested across recordings, and it
  comes from one event, which is why it is only suggested.

## Speech

A synthesizer is given words and a voice and writes a recording of them being
said:

```python
from constructive_media import speech

voice = speech.Voice("ada", emotion="warm", profile="the-name-the-server-knows-her-by")
speech.synthesizer().say("The lantern is lit at dusk.", "line.wav", voice=voice, seed=507)
```

A voice belongs to a persona and has an emotion. A line asks for a persona and
an emotion; when the persona has no voice for that emotion its neutral voice
speaks, and failing that the first it has. No voice is kept in this
repository, and no recording: a voice is a row that says which adapter speaks
it and what that adapter knows it by.

```sh
media voice "Ada Field" the-name-the-server-knows-her-by --new
media voice ada-field another-name --emotion whisper --seed 31
media voices
```

The built-in adapter reaches any server that answers the `audio/speech`
request, which many programs that run a speech model do. Nothing is sent until
you name the server and the model:

```sh
export CONSTRUCTIVE_MEDIA_URL_SPEECH=http://localhost:8080/v1
export CONSTRUCTIVE_MEDIA_MODEL_SPEECH=the-name-the-server-knows-it-by
```

`CONSTRUCTIVE_MEDIA_KEY_SPEECH` holds a key for a server that wants one.
`CONSTRUCTIVE_MEDIA_EXTRA_SPEECH` is a JSON object sent with every request,
such as `{"speed": 0.95}`. The server is asked for a WAV file; whatever else it
sends is made into one.

A speech model does not always say what it was given, and what it gets wrong
changes with the seed. So a line is said, listened back to through the
`transcription` slot, checked, and said again with another seed until a take
passes (`constructive_media.audio.line`). The seed is sent as `seed`, which is
not part of the usual request: a server that takes it gives another reading
each time, and one that refuses it can be spared it with
`CONSTRUCTIVE_MEDIA_EXTRA_SPEECH={"seed": null}`. A server that says a line the
same way whatever the seed cannot mend a bad take by saying it again.

`media say scene.conv out/scene.flac` does this for every line of a
[`.conv`](https://github.com/constructive-io/conversation-format) script and mixes the
result (`constructive_media.audio.scene`).

A voice cloned from a clip keeps where the clip is and the words said in it.
A server that clones is sent them by naming them in the extra fields, since
servers do not agree on what to call them:

```sh
export CONSTRUCTIVE_MEDIA_EXTRA_SPEECH='{"voice": null, "ref_audio": "{reference:base64}", "ref_text": "{said}"}'
```

`{reference}` is the clip's path, `{reference:base64}` the clip itself,
`{said}` its words, `{persona}` and `{emotion}` whose voice it is. A field that
names something a voice does not have is left out, so one setting serves
cloned voices and the server's own. Making a clip, auditioning a voice and
sharing voices in a library are in [VOICES.md](./VOICES.md) and
[LIBRARIES.md](./LIBRARIES.md).

### On a Mac, with no server

The `mlx` adapter runs a speech model in the engine's own process on Apple
silicon, through [mlx-audio](https://github.com/Blaizzy/mlx-audio):

```sh
pip install "constructive-media[audio]" mlx-audio
export CONSTRUCTIVE_MEDIA_ADAPTER_SPEECH=mlx
media say --voice ada "The lantern is lit at dusk." out/line.wav
```

Left to itself it loads Qwen3-TTS, which clones a voice from its clip and the
words said in it; `CONSTRUCTIVE_MEDIA_MODEL_SPEECH` names another model
mlx-audio can load, as a folder or a repository on Hugging Face. The first
line waits for the model to be fetched, a few gigabytes. A line is said at a
temperature of 0.3, which keeps a clone close to its clip;
`CONSTRUCTIVE_MEDIA_EXTRA_SPEECH='{"temperature": 0.5, "language": "french"}'`
changes what it is said with. Two models on one graphics processor at once can
stop a machine dead, so every `media` on a machine takes turns: a line is said
while holding one lock under the engine's home.

mlx-audio is MIT, and is not one of the engine's extras because it brings two
packages under MPL-2.0 with it, tqdm and certifi. You install it yourself,
knowing that; the engine needs neither.

Any other model that runs in the engine's own process is an adapter too, built
in when everything it needs is within [the rule](./LICENSING.md) and a plugin
when it is not. It is handed the voice's clip and the words said in it, and
can be chosen per voice, by the voice's `engine`.

### An adapter that can blend

[`media blend`](./VOICES.md#a-voice-between-two-voices) makes a voice between
other voices, and it needs two things more of a synthesizer than `say`. Both
are optional, and an adapter without them is told so when a blend is asked of
it:

```python
def embed(self, recording) -> list[float]:
    """A voice as the numbers this model hears it by, from a recording of it."""

def say_as(self, text, target, *, embedding, seed=None) -> Path:
    """Say `text` in the voice that `embedding` is."""
```

The engine does the arithmetic between them and never looks inside a row: it
asks only that every row from one adapter is the same size. The `mlx` adapter
has both, for a model with a speaker encoder of its own, which the default
has.

## Pictures and video

`media generate image` and `media generate video` hand a description to
whatever fills the `image` or the `video` slot. Both built-in adapters run a
model on your own machine. Neither is among the engine's extras, for the
reason mlx-audio is not: each brings packages outside
[the rule](./LICENSING.md#dependencies) with it. You install them yourself,
knowing that.

`mflux` runs the [mflux](https://github.com/filipstrand/mflux) programs (MIT),
which run image models on Apple silicon through MLX:

```sh
pip install mflux        # or: uv tool install mflux
media generate image "a harbour at dawn, fog over the water"
media generate image "the same harbour at night" --from harbour.png --size 1024x1024
```

It is run as a program, `mflux-generate-flux2`, and
`mflux-generate-flux2-edit` for a request that starts from a picture; nothing
in the engine imports it. A picture is 1344x768 and four passes unless you
say, which takes FLUX.2 klein 4B about eighteen seconds on an Apple M4 Max;
eight passes take twenty-seven and are a little richer.
`CONSTRUCTIVE_MEDIA_EXTRA_IMAGE` is a JSON object of what to make a picture
with: `program` (where the program is, when it is not on the path), `steps`,
`quantize`, `guidance`, and `base_model` for a model given as a repository or
a folder.

`diffusers` loads a Wan model in the engine's own process with
[diffusers](https://github.com/huggingface/diffusers) (Apache-2.0) on PyTorch
(BSD-3-Clause), and writes what it makes with ffmpeg:

```sh
pip install torch diffusers transformers ftfy
media generate video "fog lifting off a harbour at dawn" --seconds 2
```

A piece is 832x480, two seconds at 16 frames a second and fifteen passes
unless you say. On an Apple M4 Max that piece took four and a half minutes,
and one second took under two.
`CONSTRUCTIVE_MEDIA_EXTRA_VIDEO` is a JSON object of `steps`, `fps`,
`guidance` and `negative`, the words for what to keep out. It runs on
`CONSTRUCTIVE_MEDIA_DEVICE`, or the best the machine has.

Both hold the lock every `media` on a machine shares while a model runs: two
models on one graphics processor at once can stop a machine dead.

What an adapter does not decide is the record. Every request is a row, with
its words, model, settings and seed, whether it worked or not, and
`media generations` lists them. The same request is made once: asked again,
`media generate` prints the file it has, and `--again` makes another. What is
made is a derived file of the kind `generated`, so `media lineage` answers
for it, and `media synthetic` lists the clips of a cut that nobody filmed.

An adapter for a service that takes minutes has `submit`, `poll` and `fetch`
in place of `make`. `media generate` hands the request over and returns, and
`media collect` asks every service whether it has finished and brings in what
it made. One that can say what a request costs has `price`, and
`media generate --max-usd` refuses a request priced above it.

## Text

A `text` adapter is asked one thing at a time: `write(prompt, *, limit, as_json)`
gives back what a model wrote. One that can also answer as it goes has
`reply(messages, *, limit)`, which is handed a conversation (each message a
`role` and its `content`) and gives what is said next in pieces, each as it is
written. [`media talk`](./TALK.md) says the first sentence aloud while the
rest is still coming. The built-in `http` adapter reads a server's stream; an
adapter with no `reply` is handed the conversation as one prompt, and its
answer arrives whole.

## Denoise

A denoiser is given a recording of a voice and writes it again with less of
the noise that was under it:

```python
from constructive_media.denoise import denoiser

denoiser().clean("recording.wav", "cleaned.wav", floor=-52.0)   # floor: how loud the noise is, when it is known
```

The built-in one is ffmpeg's own (`afftdn`), which takes a steady hiss down
by ten decibels or so and leaves the voice nearly as it was. It is the last
thing `media clean` reaches for, after the filters that cost a voice nothing; see
[VOICES.md](./VOICES.md#clean-a-recording-first). A model that does better is
an adapter in this slot.

## Effects

`Effects` carries the request in the vocabulary of the
[`.conv` format](https://github.com/constructive-io/conversation-format): `reverb`
(`room`, `hall`, `cathedral`, `plate`), `reverb-mix`, `delay`,
`delay-feedback`, `delay-mix`, `compress`, `gain`. A value outside the format's
range is an error; nothing is silently ignored.

The built-in backend runs the `ffmpeg` program. ffmpeg has no algorithmic
reverb, so a space is a few echo taps: more, longer and quieter for a bigger
room. It reads as a space. For a studio reverb, install a plugin.

## Browser

A page is the easiest way to draw a slide, a caption or a lower third, so the
engine films one: see [STAGE.md](./STAGE.md). A browser opens a page and keeps
every picture it paints, with the time it was painted, until the page says it
is done; and it prints a page as a PDF.

The built-in adapter runs a Chrome or a Chromium that is already on the
machine, with no window, as a separate program driven down a pipe in the
DevTools protocol. Nothing is installed, no port is opened, and nothing of the
browser is part of this package. It looks in the usual places for Chrome,
Chromium, Edge and Brave. To name one, or to give it more flags:

```sh
export CONSTRUCTIVE_MEDIA_MODEL_BROWSER=/usr/bin/chromium
export CONSTRUCTIVE_MEDIA_EXTRA_BROWSER='{"flags": ["--no-sandbox"]}'    # in a container that needs it
```
