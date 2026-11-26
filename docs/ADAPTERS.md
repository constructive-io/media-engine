# Adapters

The engine never imports a model library or an effects library by name. It
asks for whatever fills a **slot** and is handed an object that does the job.

- Swapping a model is a setting, not a code change.
- Built-in adapters are MIT or Apache-2.0, and so is everything they import.
  The registry refuses to register a built-in under any other license.
- A tool under another license is still usable. Its adapter lives in a separate
  package, under its own license, and the engine finds it at run time.

## Slots

| Slot | Fills | Built in |
| --- | --- | --- |
| `transcription` | speech to text, with word timings | |
| `detection` | objects and people in a frame | |
| `speech` | text to speech | |
| `voice_conversion` | one voice re-spoken in another | |
| `image` | image generation | |
| `video` | video generation | |
| `lip_sync` | a face re-timed to new speech | |
| `effects` | audio effects | `ffmpeg` |
| `text` | text generation and judgement | |
| `embedding` | text and image embeddings | |

Slots fill in as the engine lands; see the [roadmap](./ROADMAP.md).

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

What an adapter must do is set by its slot. For `effects` it is
`constructive_media.effects.EffectBackend`: an object with a `name` and an
`apply(source, target, effects)` method.

## Effects

`Effects` carries the request in the vocabulary of the
[`.conv` format](https://github.com/constructive-io/conv-spec): `reverb`
(`room`, `hall`, `cathedral`, `plate`), `reverb-mix`, `delay`,
`delay-feedback`, `delay-mix`, `compress`, `gain`. A value outside the format's
range is an error; nothing is silently ignored.

The built-in backend runs the `ffmpeg` program. ffmpeg has no algorithmic
reverb, so a space is a few echo taps: more, longer and quieter for a bigger
room. It reads as a space. For a studio reverb, install a plugin.
