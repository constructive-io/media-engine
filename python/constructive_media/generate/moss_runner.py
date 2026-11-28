"""One sound from one description, by MOSS-SoundEffect. Run apart from the engine.

This file is a program, not part of the package that holds it: the `moss`
adapter runs it with an interpreter that has mlx-speech, which the engine's
own need not be. So it imports nothing of the engine's.

    python -P moss_runner.py '{"prompt": "rain on a tin roof", "seconds": 4, "seed": 7,
                               "model": "…", "codec": "…", "out": "rain.wav"}'

`model` and `codec` are each a folder, or a repository on Hugging Face that
is fetched the first time. The last line it prints is JSON: what was written,
or `{"error": …}` beside a status that is not 0.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def folder(named: str) -> Path:
    """Where a model's weights are: in the folder named, or in the one folder inside it that has them."""

    def weights(here: Path) -> Path | None:
        if any(here.glob("*.safetensors")):
            return here
        inside = sorted({one.parent for one in here.glob("*/*.safetensors")})
        return inside[0] if inside else None

    here = Path(named).expanduser()
    if here.is_dir():
        found = weights(here)
        if found is None:
            raise FileNotFoundError(f"{here} holds no model weights")
        return found
    from huggingface_hub import snapshot_download

    try:
        found = weights(Path(snapshot_download(named, local_files_only=True)))
    except Exception:
        found = None
    if found is None:
        found = weights(Path(snapshot_download(named)))
    if found is None:
        raise FileNotFoundError(f"{named} holds no model weights")
    return found


def made(asked: dict) -> dict:
    import mlx.core as mx
    from mlx_speech.audio import write_wav
    from mlx_speech.generation import MossTTSDelayGenerationConfig, synthesize_moss_tts_delay_conversations
    from mlx_speech.models.moss_audio_tokenizer import load_moss_audio_tokenizer_model
    from mlx_speech.models.moss_delay import MossTTSDelayProcessor, load_moss_sound_effect_model
    from mlx_speech.models.moss_delay.sound_effect import build_sound_effect_conversation

    model = load_moss_sound_effect_model(folder(asked["model"]))
    codec = load_moss_audio_tokenizer_model(folder(asked["codec"]))
    processor = MossTTSDelayProcessor.from_path(model.model_dir, audio_tokenizer=codec.model)
    conversation, tokens = build_sound_effect_conversation(
        processor, ambient_sound=asked["prompt"], duration_seconds=float(asked["seconds"]), expected_tokens=None)
    how = MossTTSDelayGenerationConfig(
        max_new_tokens=4096, use_kv_cache=True, do_sample=True,
        audio_temperature=1.5, audio_top_p=0.6, audio_top_k=50, audio_repetition_penalty=1.1,
    )
    # The library takes no seed, and without one two takes of the same words are two different sounds.
    mx.random.seed(int(asked["seed"]))
    done = synthesize_moss_tts_delay_conversations(model.model, processor, conversations=conversation,
                                                   mode="generation", config=how)
    sound = done.outputs[0]
    if sound.waveform is None or not int(sound.waveform.shape[-1]):
        raise RuntimeError("the model made nothing that can be heard")
    out = Path(asked["out"])
    out.parent.mkdir(parents=True, exist_ok=True)
    write_wav(out, sound.waveform, sample_rate=sound.sample_rate)
    return {"out": str(out), "rate": int(sound.sample_rate), "tokens": int(tokens),
            "seconds": round(int(sound.waveform.shape[-1]) / float(sound.sample_rate), 3)}


def main(given: list[str]) -> int:
    try:
        print(json.dumps(made(json.loads(given[0]))))
    except BaseException as error:
        if isinstance(error, KeyboardInterrupt):
            raise
        print(json.dumps({"error": f"{type(error).__name__}: {error}"}))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
