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
| `transcription` | speech to text, with word timings | |
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
| `detection` | `constructive_media.detection.Detector` | `detect(images, confidence)` returns the detections for each image, boxes as shares of the frame |
| `effects` | `constructive_media.effects.EffectBackend` | `apply(source, target, effects)` writes the audio with the effects applied |

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
