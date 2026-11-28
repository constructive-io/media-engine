# Licensing rule

This repository is MIT licensed. The rule below keeps it that way.

## What may live here

| Thing | Allowed |
| --- | --- |
| Code written for this repository | MIT |
| Code copied in from elsewhere | MIT, Apache-2.0 or BSD only, with its notice kept |
| Dependencies the engine asks for | MIT, Apache-2.0 or BSD only |
| Any package the code names in an import, asked for or not | MIT, Apache-2.0 or BSD only |
| What those dependencies bring with them | Any permissive license; never copyleft |
| Copyleft code or dependencies (GPL, AGPL, LGPL, MPL and the like) | Never |
| Non-commercial or research-only code | Never |
| Model weights, voices, audio, images, footage | Never, under any license |

`tests/test_licenses.py` enforces the dependency rows: it reads
`pyproject.toml`, looks up what each installed package declares, and fails on
anything outside the rule or anything that declares no license at all. It
also reads every import written in the engine's code and fails on a package
that is not in its list of the ones that have been read and found to meet the
rule.

Anything else goes in a [plugin](#adapters-and-plugins), in another
repository, under its own license.

## Dependencies

| Package | License | Used for | Installed by |
| --- | --- | --- | --- |
| `pg8000` | BSD-3-Clause | The PostgreSQL driver | the core |
| `numpy` | BSD-3-Clause | Arithmetic on pixels and on sound | `[video]`, `[detection]`, `[speakers]`, `[audio]` |
| `pillow` | MIT-CMU | Reading image files | `[detection]` |
| `onnxruntime` | MIT | Running the default detector and the speaker models | `[detection]`, `[speakers]` |
| `torch`, `torchvision` | BSD-3-Clause | The second detector | `[torchvision]` |
| `onnx` | Apache-2.0 | Building a small model in the tests | `[test]` |

Four things the engine can use are installed by you and never by the engine.
Each is under a license the rule allows, and the engine's code names nothing
in an import that is not. Each also brings packages of its own choosing with
it, and two of those are outside the rule: `tqdm` (MPL-2.0 and MIT) and
`certifi` (MPL-2.0), which come with anything that fetches a model from
Hugging Face. The engine does not contain them, ask for them or import them,
so none of the four is an extra and nothing else in the engine needs any of
them:

- `mlx-audio` (MIT), which the `mlx` speech adapter runs a model through on
  Apple silicon.
- `mflux` (MIT), whose programs the `mflux` image adapter runs. It is run as
  a program and never imported, and is best installed apart
  (`uv tool install mflux`): it also brings OpenCV's wheels, which bundle
  LGPL builds of the FFmpeg libraries.
- `diffusers`, `transformers` and `ftfy` (Apache-2.0), which the `diffusers`
  video adapter loads a model with, on `torch`.

The fourth is what the `kokoro` speech adapter needs besides `mlx-audio`.
What it would otherwise bring that is copyleft, the adapter does without:

- `misaki` (Apache-2.0), which turns English into the sounds the model is
  handed, with `spacy` (MIT) and spaCy's `en_core_web_sm` (MIT).
- Not `num2words`, which is LGPL and which misaki imports by name to write
  a number out in words. Where it is not installed the engine's own
  (`speech/numerals.py`, MIT) stands in its place and says the same words
  for English.
- Not `phonemizer-fork` or `espeakng-loader`, which are GPL-3.0 and which
  misaki would hand a word it does not know to. The adapter reads such a word
  itself, and does not ask for them. `pip install "misaki[en]"` brings them:
  install `misaki` plain, as [ADAPTERS.md](./ADAPTERS.md#a-small-quick-model)
  has it, if you do not want them.

Left out on purpose, each replaced by something that meets the rule:

| Not used | Why | Instead |
| --- | --- | --- |
| `psycopg`, `psycopg2` | LGPL | `pg8000` |
| Ultralytics YOLO | AGPL-3.0 | D-FINE (Apache-2.0) through ONNX Runtime |
| OpenCV's Python wheels | They bundle LGPL builds of the FFmpeg libraries | The `ffmpeg` program and numpy |
| PySceneDetect | Needs OpenCV | The cut detector in `video/cuts.py` |
| `soundfile`, and `librosa`, which needs it | They load libsndfile, which is LGPL | The `ffmpeg` program to read sound, and the writer in `audio/pcm.py` |
| `pedalboard` | GPL-3.0 | The `effects` slot, with `ffmpeg` behind it |
| `num2words` | LGPL | The number words in `speech/numerals.py` |
| `phonemizer-fork`, `espeakng-loader` | GPL-3.0 | The reader of unknown words in `speech/sounding.py` |

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
Six are there now:

| Plugin | Slot | Why it is not here |
| --- | --- | --- |
| `pedalboard` | `effects` | The library is GPL-3.0 |
| `ultralytics` | `detection` | The library is AGPL-3.0 |
| `seedvc` | `voice_conversion` | The tool is GPL-3.0 |
| `ltx` | `video` | The model is under its maker's own terms |
| `demucs` | `separation` | The weights are for research only, and it needs an LGPL encoder |
| `latentsync` | `lip_sync` | The weights carry use restrictions, and its face models are for non-commercial research |

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
