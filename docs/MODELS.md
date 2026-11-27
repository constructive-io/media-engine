# Models

The engine ships no model. Each one below is downloaded by whoever runs it, the
first time it is asked for, into the folder `CONSTRUCTIVE_MEDIA_MODELS_DIR`
names. Every download is pinned to one revision of one published file and to
the SHA-256 its bytes must have; a file that does not match is refused. The
exact address and hash of each are in the module named under its heading.

Only models under MIT, Apache-2.0 or BSD terms are known by name here. A
model's license binds whoever downloads and runs it, not this repository, and
weights are trained on datasets that carry terms of their own; whether those
matter for your use is yours to decide. See [the licensing rule](./LICENSING.md).

A slot's model is chosen with `CONSTRUCTIVE_MEDIA_MODEL_<SLOT>`, by one of the
names below or, for most adapters, by the path of a file of your own.

## Transcription: speech to text

`transcription/whispercpp.py`. [Whisper](https://github.com/openai/whisper) is
OpenAI's, published under MIT; the files are the same weights in the format
[whisper.cpp](https://github.com/ggml-org/whisper.cpp) reads.

| Name | What it is | License | Published at |
| --- | --- | --- | --- |
| `large-v3-turbo` (default) | Whisper large v3 turbo, 1.6 GB: the most accurate at a speed worth running everywhere | MIT | [ggerganov/whisper.cpp](https://huggingface.co/ggerganov/whisper.cpp) |
| `large-v3-turbo-q5_0` | Whisper large v3 turbo squeezed to 574 MB | MIT | [ggerganov/whisper.cpp](https://huggingface.co/ggerganov/whisper.cpp) |
| `large-v3` | Whisper large v3, 3.1 GB: the full model, several times slower than turbo | MIT | [ggerganov/whisper.cpp](https://huggingface.co/ggerganov/whisper.cpp) |
| `medium` | Whisper medium, 1.5 GB | MIT | [ggerganov/whisper.cpp](https://huggingface.co/ggerganov/whisper.cpp) |
| `medium.en` | Whisper medium, English only | MIT | [ggerganov/whisper.cpp](https://huggingface.co/ggerganov/whisper.cpp) |
| `small` | Whisper small, 466 MB | MIT | [ggerganov/whisper.cpp](https://huggingface.co/ggerganov/whisper.cpp) |
| `small.en` | Whisper small, English only | MIT | [ggerganov/whisper.cpp](https://huggingface.co/ggerganov/whisper.cpp) |
| `base` | Whisper base, 142 MB: fast on a machine with no graphics card | MIT | [ggerganov/whisper.cpp](https://huggingface.co/ggerganov/whisper.cpp) |
| `base.en` | Whisper base, English only | MIT | [ggerganov/whisper.cpp](https://huggingface.co/ggerganov/whisper.cpp) |
| `tiny` | Whisper tiny, 75 MB: the fastest and the least accurate | MIT | [ggerganov/whisper.cpp](https://huggingface.co/ggerganov/whisper.cpp) |
| `tiny.en` | Whisper tiny, English only | MIT | [ggerganov/whisper.cpp](https://huggingface.co/ggerganov/whisper.cpp) |

One more model is fetched with any of them: `ggml-silero-v6.2.0.bin`,
[Silero VAD](https://github.com/snakers4/silero-vad) (MIT) in whisper.cpp's
format, from [ggml-org/whisper-vad](https://huggingface.co/ggml-org/whisper-vad).
It finds where the speech is, so that only speech is transcribed.

`media talk` listens to a microphone with the same model in another form:
[Silero VAD](https://github.com/snakers4/silero-vad) (MIT) as an ONNX file of
two megabytes, from [onnx-community/silero-vad](https://huggingface.co/onnx-community/silero-vad),
run through ONNX Runtime (`talk/hearing.py`). It says when someone has started
to speak and when they have stopped. Without NumPy and ONNX Runtime it is not
fetched, and speech is told from a room by how loud it is: see [TALK.md](./TALK.md).

## Detection: objects and people in a frame

`detection/onnx.py`. [D-FINE](https://github.com/Peterande/D-FINE) is published
under Apache-2.0, and so are its COCO models
([nano](https://huggingface.co/ustc-community/dfine-nano-coco),
[small](https://huggingface.co/ustc-community/dfine-small-coco)). The files
are those models exported to ONNX; the pages they are published at name the
model each was made from and state no license of their own.

| Name | What it is | License | Published at |
| --- | --- | --- | --- |
| `dfine_n` (default) | D-FINE nano, trained on COCO: the fastest | Apache-2.0 | [onnx-community/dfine_n_coco-ONNX](https://huggingface.co/onnx-community/dfine_n_coco-ONNX) |
| `dfine_s` | D-FINE small, trained on COCO: finds more, at about half the speed | Apache-2.0 | [onnx-community/dfine_s_coco-ONNX](https://huggingface.co/onnx-community/dfine_s_coco-ONNX) |

The second detection adapter, `torchvision`, takes any detection model
[torchvision](https://github.com/pytorch/vision) knows by name, such as
`fasterrcnn_resnet50_fpn_v2` (its default here) or
`fasterrcnn_mobilenet_v3_large_320_fpn`. torchvision is BSD-3-Clause and
downloads the weights itself; it notes that pre-trained weights may carry terms
that come from the data they were trained on.

## Speakers: whose voice is whose

`speakers/onnx.py`. The models are [WeSpeaker](https://github.com/wenet-e2e/wespeaker)'s,
trained on VoxCeleb and published under Apache-2.0, as ONNX files.

| Name | What it is | License | Published at |
| --- | --- | --- | --- |
| `campplus` (default) | CAM++: the fastest, and as good as any on a second or two of speech | Apache-2.0 | [Wespeaker/wespeaker-voxceleb-campplus](https://huggingface.co/Wespeaker/wespeaker-voxceleb-campplus) |
| `resnet34` | ResNet34: as good, at about a third of the speed | Apache-2.0 | [Wespeaker/wespeaker-voxceleb-resnet34](https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34) |
| `resnet221_lm` | ResNet221: the surest that two voices are two, at a twelfth of the speed | Apache-2.0 | [Wespeaker/wespeaker-voxceleb-resnet221-LM](https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet221-LM) |

## Speech: text to a voice

The `http` adapter names no model: the server it reaches has its own. The
`mlx` adapter, which runs one on a Mac, loads Qwen3-TTS when no other is named:
`mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16`, a conversion for MLX of
[Qwen3-TTS](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base), published
under Apache-2.0. It is fetched by mlx-audio the first time a line is said,
and it clones a voice from a clip of 15 to 28 seconds and the words said in it.

## Pictures: a description to an image

`generate/mflux.py`. The `mflux` adapter runs the
[mflux](https://github.com/filipstrand/mflux) programs, which run image models
on Apple silicon. It asks for `flux2-klein-4b` when no other model is named:
[FLUX.2 klein 4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B),
published under Apache-2.0, which mflux fetches the first time a picture is
made. It makes a picture in four passes, and it can start from a picture as
well as from words.

`CONSTRUCTIVE_MEDIA_MODEL_IMAGE` takes anything else mflux can run: a name it
knows, a repository or a folder. Some of those, the larger FLUX models among
them, are published under terms that do not allow commercial use. None is
named here, and which one runs is yours to choose and to check.

## Video: a description to moving pictures

`generate/diffusers.py`. The `diffusers` adapter loads
`Wan-AI/Wan2.1-T2V-1.3B-Diffusers` when no other model is named:
[Wan 2.1 T2V 1.3B](https://huggingface.co/Wan-AI/Wan2.1-T2V-1.3B-Diffusers),
published under Apache-2.0, in the layout
[diffusers](https://github.com/huggingface/diffusers) reads. It is the
smallest of the Wan models: 832x480 at 16 frames a second, from words alone.
diffusers fetches it the first time a piece is made.
`CONSTRUCTIVE_MEDIA_MODEL_VIDEO` takes a folder or another repository that
holds a Wan model of the same kind.

## Models you bring

Three slots reach a model through a server you run or rent, and know no model
by name. Which model answers, and under what terms, is yours to choose and to
check.

| Slot | What the server must answer | Set with |
| --- | --- | --- |
| `speech` | the `audio/speech` request | `CONSTRUCTIVE_MEDIA_URL_SPEECH`, `CONSTRUCTIVE_MEDIA_MODEL_SPEECH` |
| `text` | the `chat/completions` request | `CONSTRUCTIVE_MEDIA_URL_TEXT`, `CONSTRUCTIVE_MEDIA_MODEL_TEXT` |
| `embedding` | the `embeddings` request | `CONSTRUCTIVE_MEDIA_URL_EMBEDDING`, `CONSTRUCTIVE_MEDIA_MODEL_EMBEDDING` |

A model that runs inside the engine's own process under terms outside the rule
is reached through a plugin, never from this repository; see
[docs/ADAPTERS.md](./ADAPTERS.md).

## Left out on purpose

| Not named | Why |
| --- | --- |
| Ultralytics YOLO models | AGPL-3.0 |
| WeSpeaker's `resnet34-LM` | Published under CC-BY-4.0, which is outside the rule |

## Slots with no model yet

`voice_conversion` and `lip_sync` have no built-in adapter. Nothing here makes
a sound from a description either: a `.conv` sound given as
a `prompt:` needs a `file:` until there is a slot for it.
