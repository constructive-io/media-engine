"""Text models reached over HTTP, on any server that speaks the usual API.

Most programs that run a language model answer the same two requests:
`chat/completions` for writing and `embeddings` for numbers. llama.cpp's
server, Ollama and vLLM all do, and so do hosted services. So one small client
reaches every one of them, and which model reads your transcripts, and on
whose machine, is a setting:

    CONSTRUCTIVE_MEDIA_URL_TEXT=http://localhost:8080/v1
    CONSTRUCTIVE_MEDIA_MODEL_TEXT=the-name-the-server-knows-it-by

and the same pair ending `_EMBEDDING`, with `CONSTRUCTIVE_MEDIA_KEY_TEXT` and
`CONSTRUCTIVE_MEDIA_KEY_EMBEDDING` for a server that wants a key. There is no
default for either: a transcript is not sent anywhere it was not told to go.

Where servers differ is in what else they can be told, and that is a setting
too. `CONSTRUCTIVE_MEDIA_EXTRA_TEXT` is a JSON object sent with every request,
for whatever a server calls its own options. The one that matters most: a
model that thinks before it answers will spend everything it is allowed on
thinking and answer nothing. Either it is told not to think, which each
server has its own way of being told, or it is allowed enough for both:

    CONSTRUCTIVE_MEDIA_EXTRA_TEXT={"reasoning_effort": "none"}
    CONSTRUCTIVE_MEDIA_EXTRA_TEXT={"max_tokens": 2000}

What is set there is sent in place of what would have been sent, all but the
model, the prompt and that the answer is to come whole.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from ..servers import Server
from . import TextError


class _Server(Server):
    error = TextError
    extra_example = '{"reasoning_effort": "none"}'


class ChatServer(_Server):
    """A model that writes, on a server that answers `chat/completions`."""

    slot, serves, path = "text", "text", "chat/completions"

    def write(self, prompt: str, *, limit: int = 200, as_json: bool = False) -> str:
        body: dict[str, Any] = {"temperature": 0, "max_tokens": limit}
        if as_json:
            body["response_format"] = {"type": "json_object"}
        body = {**body, **self.extra, "model": self.model, "stream": False,
                "messages": [{"role": "user", "content": prompt}]}
        answer = self._ask(body)
        try:
            choice = answer["choices"][0]
            written = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise TextError(f"the text model at {self.url} answered without a message in it") from None
        if not written.strip() and choice.get("finish_reason") == "length":
            # Not an answer that could not be read: every passage would go the same way.
            raise TextError(
                f"{self.model} used all {body.get('max_tokens', limit)} tokens it was allowed and answered nothing. "
                "A model that thinks before it answers has to be told not to, in whatever way its server "
                f"takes that, or allowed enough for both. Set {self._extra_variable} to "
                '{"reasoning_effort": "none"} for a server that takes that, or to {"max_tokens": 2000}')
        return written


class EmbeddingServer(_Server):
    """A model that turns text into numbers, on a server that answers `embeddings`."""

    slot, serves, path = "embedding", "embedding", "embeddings"

    def embed(self, texts: Sequence[str], *, asking: bool = False) -> list[list[float]]:
        # A question is sent as it was asked: the API has no way to say that it is one.
        texts = list(texts)
        if not texts:
            return []
        answer = self._ask({**self.extra, "model": self.model, "input": texts})
        try:
            rows = sorted(answer["data"], key=lambda row: row.get("index", 0))
            found = [[float(number) for number in row["embedding"]] for row in rows]
        except (KeyError, TypeError, ValueError, AttributeError):
            raise TextError(f"the embedding model at {self.url} answered without embeddings in it") from None
        if len(found) != len(texts) or not all(row and all(map(math.isfinite, row)) for row in found):
            raise TextError(f"the embedding model at {self.url} was sent {len(texts)} texts and "
                            f"answered with {len(found)} embeddings, not all of them usable")
        return found
