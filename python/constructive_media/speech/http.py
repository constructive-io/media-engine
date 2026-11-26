"""Speech from a server, on any that speaks the usual API.

Many programs that run a speech model answer the same request,
`audio/speech`: the words, the name of a voice, and the kind of file wanted
back. One small client reaches every one of them, and which model speaks,
and on whose machine, is a setting:

    CONSTRUCTIVE_MEDIA_URL_SPEECH=http://localhost:8080/v1
    CONSTRUCTIVE_MEDIA_MODEL_SPEECH=the-name-the-server-knows-it-by

with `CONSTRUCTIVE_MEDIA_KEY_SPEECH` for a server that wants a key. There is
no default: a script is not sent anywhere it was not told to go.

A voice's `profile` is sent as the voice's name, so it must be a name the
server knows. The server is asked for a WAV file; whatever it sends instead
is made into one.

A line that came out wrong is said again with another seed, so the seed is
sent, as `seed`. That is not part of the usual API. A server that takes it
gives a different reading each time; one that ignores it may say the line
the same way again; one that refuses it can be spared it:

    CONSTRUCTIVE_MEDIA_EXTRA_SPEECH={"seed": null}

`CONSTRUCTIVE_MEDIA_EXTRA_SPEECH` is a JSON object sent with every request,
for whatever a server calls its own options, such as `{"speed": 0.95}`.
What is set there is sent in place of what would have been sent, all but
the model and the words, and what is set to null is not sent at all.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .. import ffmpeg
from ..servers import Server
from . import SpeechError, Voice


class SpeechServer(Server):
    """A model that speaks, on a server that answers `audio/speech`."""

    slot, serves, path = "speech", "speech", "audio/speech"
    error = SpeechError
    extra_example = '{"speed": 0.95}'

    def say(self, text: str, target: Path, *, voice: Voice, seed: int | None = None) -> Path:
        if not voice.profile:
            raise SpeechError(f"the {voice.emotion} voice of {voice.persona!r} has no profile: "
                              "the name the speech server knows the voice by")
        body = {"voice": voice.profile, "response_format": "wav", "seed": seed, **self.extra,
                "model": self.model, "input": text}
        sound = self._send({key: value for key, value in body.items() if value is not None})
        if len(sound) < 64:
            raise SpeechError(f"the speech model at {self.url} answered with nothing to hear")
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        if sound[:4] == b"RIFF" and sound[8:12] == b"WAVE":
            target.write_bytes(sound)
            return target
        # Asked for a WAV file and sent something else: ffmpeg knows what it is.
        handle, name = tempfile.mkstemp(prefix="said-", dir=target.parent)
        try:
            with os.fdopen(handle, "wb") as file:
                file.write(sound)
            try:
                ffmpeg.run(ffmpeg.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", name,
                           "-vn", "-f", "wav", target)
            except ffmpeg.FfmpegError:
                raise SpeechError(f"the speech model at {self.url} answered with something that is not sound") from None
        finally:
            os.unlink(name)
        return target
