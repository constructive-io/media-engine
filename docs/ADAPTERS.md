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
| `speech` | text to speech | |
| `voice_conversion` | one voice re-spoken in another | |
| `image` | image generation | |
| `video` | video generation | |
| `lip_sync` | a face re-timed to new speech | |
| `effects` | audio effects | `ffmpeg` |
| `text` | text generation and judgement | |
| `embedding` | text and image embeddings | |

Slots fill in as the engine lands; see the [roadmap](./ROADMAP.md). `me adapters`
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
| `effects` | `constructive_media.effects.EffectBackend` | `apply(source, target, effects)` writes the audio with the effects applied |

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
- **A vocabulary is shown where it is safe.** `me vocabulary` holds the names
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
`tiny` are never shown one. `me transcribe --no-vocabulary` leaves it out.

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

## Effects

`Effects` carries the request in the vocabulary of the
[`.conv` format](https://github.com/constructive-io/conv-spec): `reverb`
(`room`, `hall`, `cathedral`, `plate`), `reverb-mix`, `delay`,
`delay-feedback`, `delay-mix`, `compress`, `gain`. A value outside the format's
range is an error; nothing is silently ignored.

The built-in backend runs the `ffmpeg` program. ffmpeg has no algorithmic
reverb, so a space is a few echo taps: more, longer and quieter for a bigger
room. It reads as a space. For a studio reverb, install a plugin.
