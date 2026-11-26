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
| `numpy` | BSD-3-Clause | Arithmetic on pixels | `[video]` |
| `pillow` | MIT-CMU | Reading image files | `[detection]` |
| `onnxruntime` | MIT | Running the default detector | `[detection]` |
| `torch`, `torchvision` | BSD-3-Clause | The second detector | `[torchvision]` |
| `onnx` | Apache-2.0 | Building a small model in the tests | `[test]` |

Left out on purpose, each replaced by something that meets the rule:

| Not used | Why | Instead |
| --- | --- | --- |
| `psycopg`, `psycopg2` | LGPL | `pg8000` |
| Ultralytics YOLO | AGPL-3.0 | D-FINE (Apache-2.0) through ONNX Runtime |
| OpenCV's Python wheels | They bundle LGPL builds of the FFmpeg libraries | The `ffmpeg` program and numpy |
| PySceneDetect | Needs OpenCV | The cut detector in `video/cuts.py` |

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

An adapter for a tool that does not meet the rule lives in a separate plugin
repository under that tool's license. The engine never imports a plugin by
name. It discovers the ones you have installed.

## Models

The engine ships no weights. A model is downloaded by whoever runs it, and that
model's license binds them, not this repository. Models the code knows by name
are listed with their license where they are defined, and only permissively
licensed ones are named:

| Model | License | Where it is defined |
| --- | --- | --- |
| OpenAI's Whisper, in whisper.cpp's file format | MIT | `transcription/whispercpp.py` |
| Silero VAD, which finds where the speech is | MIT | `transcription/whispercpp.py` |
| D-FINE nano and small, trained on COCO | Apache-2.0 | `detection/onnx.py` |
| torchvision's detection models, trained on COCO | BSD-3-Clause | chosen by name from torchvision |

Weights are trained on datasets that carry terms of their own. Whether those
matter for your use is yours to decide.

## Programs the engine calls

`ffmpeg`, `ffprobe`, whisper.cpp's `whisper-cli` and
`whisper-vad-speech-segments`, and PostgreSQL are run as separate programs.
They are not part of this repository, are not linked into it, and keep their
own licenses: FFmpeg is LGPL or GPL depending on how your copy was built,
whisper.cpp is MIT, PostgreSQL is under the PostgreSQL License. You install
them yourself.
