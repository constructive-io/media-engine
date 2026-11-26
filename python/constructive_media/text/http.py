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

import json
import math
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

from ..settings import ENV_PREFIX, Settings
from . import TextError

TIMEOUT_S = 180.0
#: An example of where a server usually is, for the message that asks for one.
EXAMPLE = "http://localhost:8080/v1"


class _Server:
    slot = ""
    #: What kind of model it is, in words, and the request it answers.
    serves = ""
    path = ""

    def __init__(
        self, model: str | None = None, url: str | None = None, key: str | None = None, *,
        settings: Settings | None = None, timeout_s: float = TIMEOUT_S,
    ) -> None:
        settings = settings or Settings.from_env()
        variable = ENV_PREFIX + "{}_" + self.slot.upper()
        self.url = (url or settings.url(self.slot) or "").rstrip("/")
        if not self.url:
            raise TextError(f"nothing says where the {self.serves} model is. Set {variable.format('URL')} to a "
                            f"server that speaks the {self.path} API, such as {EXAMPLE}")
        if urlsplit(self.url).scheme not in ("http", "https") or not urlsplit(self.url).netloc:
            raise TextError(f"{variable.format('URL')} must be an http or https address, such as {EXAMPLE}")
        self.model = model or settings.model(self.slot) or ""
        if not self.model:
            raise TextError(f"nothing says which {self.serves} model to ask for. Set {variable.format('MODEL')} "
                            "to the name the server knows it by")
        self.name = "http"
        self.timeout_s = timeout_s
        self._key = key or settings.key(self.slot)
        try:
            self.extra = json.loads(settings.extra(self.slot) or "{}")
        except ValueError:
            self.extra = None
        if not isinstance(self.extra, dict):
            raise TextError(f"{variable.format('EXTRA')} must be a JSON object, such as "
                            '{"reasoning_effort": "none"}')
        self._extra_variable = variable.format("EXTRA")

    def _ask(self, body: dict[str, Any]) -> Any:
        headers = {"Content-Type": "application/json"}
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        request = urllib.request.Request(  # noqa: S310 - the scheme was checked when this was made
            f"{self.url}/{self.path}", data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as answer:  # noqa: S310
                return json.loads(answer.read())
        except urllib.error.HTTPError as error:
            with error:
                said = " ".join(error.read(600).decode("utf-8", "replace").split())[:300]
            raise TextError(f"the {self.serves} model at {self.url} refused with {error.code}"
                            + (f": {said}" if said else "")) from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise TextError(f"the {self.serves} model at {self.url} did not answer: "
                            f"{getattr(error, 'reason', None) or error}") from None
        except ValueError:
            raise TextError(f"the {self.serves} model at {self.url} answered with something that is not JSON") from None


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
