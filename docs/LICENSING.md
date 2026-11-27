# Licensing rule

This repository is MIT licensed. The rule below keeps it that way.

## What may live here

| Thing | Allowed |
| --- | --- |
| Code written for this repository | MIT |
| Code copied in from elsewhere | MIT, Apache-2.0 or BSD only, with its notice kept |
| Dependencies the engine asks for | MIT, Apache-2.0 or BSD only |
| What those dependencies bring with them | Any permissive license; never copyleft |
| Copyleft code or dependencies (GPL, AGPL, LGPL, MPL and the like) | Never |
| Non-commercial or research-only code | Never |
| Model weights, voices, audio, images, footage | Never, under any license |

`tests/test_licenses.py` enforces the dependency rows: it reads
`pyproject.toml`, looks up what each installed package declares, and fails on
anything outside the rule or anything that declares no license at all.

## Dependencies

| Package | License | Used for | Installed by |
| --- | --- | --- | --- |
| `pg8000` | BSD-3-Clause | The PostgreSQL driver | the core |
| `numpy` | BSD-3-Clause | Arithmetic on pixels and on sound | `[video]`, `[detection]`, `[speakers]`, `[audio]` |
| `pillow` | MIT-CMU | Reading image files | `[detection]` |
| `onnxruntime` | MIT | Running the default detector and the speaker models | `[detection]`, `[speakers]` |
| `torch`, `torchvision` | BSD-3-Clause | The second detector | `[torchvision]` |
| `onnx` | Apache-2.0 | Building a small model in the tests | `[test]` |

One thing the engine can use is installed by you and never by the engine:
`mlx-audio` (MIT), which the `mlx` speech adapter runs a model through on
Apple silicon. It brings `tqdm` (MPL-2.0 and MIT) and `certifi` (MPL-2.0)
with it, which are outside the rule, so it is not an extra and nothing in the
engine needs it.

Left out on purpose, each replaced by something that meets the rule:

| Not used | Why | Instead |
| --- | --- | --- |
| `psycopg`, `psycopg2` | LGPL | `pg8000` |
| Ultralytics YOLO | AGPL-3.0 | D-FINE (Apache-2.0) through ONNX Runtime |
| OpenCV's Python wheels | They bundle LGPL builds of the FFmpeg libraries | The `ffmpeg` program and numpy |
| PySceneDetect | Needs OpenCV | The cut detector in `video/cuts.py` |
| `soundfile`, and `librosa`, which needs it | They load libsndfile, which is LGPL | The `ffmpeg` program to read sound, and the writer in `audio/pcm.py` |
| `pedalboard` | GPL-3.0 | The `effects` slot, with `ffmpeg` behind it |

Binary wheels are built by their own projects and can bundle more than the
package's own code. Two things worth knowing before you redistribute an
environment: numerical wheels on Linux commonly bundle compiler runtime
libraries under their own terms, and PyTorch installed from PyPI on Linux
brings NVIDIA's proprietary CUDA packages (install from the PyTorch CPU index
to avoid them). Neither is part of this repository.

## Adapters and plugins

The engine reaches the database, every model and every effect through an
adapter. Each slot's built-in adapter meets the rule above, and the registry
refuses to register a built-in under any other license.

An adapter for a tool that does not meet the rule lives in a separate
repository, [media-engine-plugins](https://github.com/constructive-io/media-engine-plugins),
as a package of its own under that tool's license. The engine never imports a
plugin by name. It discovers the ones you have installed, never makes one the
default, and `media adapters` shows each with its license and where it came from.
Two are there now: `pedalboard` for the `effects` slot (GPL-3.0) and
`ultralytics` for the `detection` slot (AGPL-3.0).

## Models

The engine ships no weights. A model is downloaded by whoever runs it, and that
model's license binds them, not this repository. Models the code knows by name
are listed with their license where they are defined, and only permissively
licensed ones are named. Each of them, with where it is published, is in
[MODELS.md](./MODELS.md):

| Model | License | Where it is defined |
| --- | --- | --- |
| OpenAI's Whisper, in whisper.cpp's file format | MIT | `transcription/whispercpp.py` |
| Silero VAD, which finds where the speech is | MIT | `transcription/whispercpp.py` |
| D-FINE nano and small, trained on COCO | Apache-2.0 | `detection/onnx.py` |
| torchvision's detection models, trained on COCO | BSD-3-Clause | chosen by name from torchvision |
| WeSpeaker's CAM++, ResNet34 and ResNet221, trained on VoxCeleb | Apache-2.0 | `speakers/onnx.py` |

Weights are trained on datasets that carry terms of their own. Whether those
matter for your use is yours to decide.

## Programs the engine calls

`ffmpeg`, `ffprobe`, whisper.cpp's `whisper-cli` and
`whisper-vad-speech-segments`, and PostgreSQL are run as separate programs.
They are not part of this repository, are not linked into it, and keep their
own licenses: FFmpeg is LGPL or GPL depending on how your copy was built,
whisper.cpp is MIT, PostgreSQL is under the PostgreSQL License. You install
them yourself.
