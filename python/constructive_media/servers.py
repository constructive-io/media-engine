"""Models on a server: the part every client for one has in common.

A model that is reached over HTTP is found by three settings named for the
slot it fills, such as `speech`:

    CONSTRUCTIVE_MEDIA_URL_SPEECH     where the server is
    CONSTRUCTIVE_MEDIA_MODEL_SPEECH   the name the server knows the model by
    CONSTRUCTIVE_MEDIA_KEY_SPEECH     a key, for a server that wants one

and `CONSTRUCTIVE_MEDIA_EXTRA_SPEECH`, a JSON object sent with every request,
for whatever a server calls its own options. There is no default for where a
server is: nothing is sent anywhere it was not told to go. A key is never
shown, in a message or anywhere else.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

from .settings import ENV_PREFIX, Settings

TIMEOUT_S = 180.0
#: An example of where a server usually is, for the message that asks for one.
EXAMPLE = "http://localhost:8080/v1"


class Server:
    """A client for one kind of model on one server."""

    slot = ""
    #: What kind of model it is, in words, and the request it answers.
    serves = ""
    path = ""
    #: What is raised when the server cannot be reached or does not answer.
    error: type[Exception] = RuntimeError

    def __init__(
        self, model: str | None = None, url: str | None = None, key: str | None = None, *,
        settings: Settings | None = None, timeout_s: float = TIMEOUT_S,
    ) -> None:
        settings = settings or Settings.from_env()
        variable = ENV_PREFIX + "{}_" + self.slot.upper()
        self.url = (url or settings.url(self.slot) or "").rstrip("/")
        if not self.url:
            raise self.error(f"nothing says where the {self.serves} model is. Set {variable.format('URL')} to a "
                             f"server that speaks the {self.path} API, such as {EXAMPLE}")
        if urlsplit(self.url).scheme not in ("http", "https") or not urlsplit(self.url).netloc:
            raise self.error(f"{variable.format('URL')} must be an http or https address, such as {EXAMPLE}")
        self.model = model or settings.model(self.slot) or ""
        if not self.model:
            raise self.error(f"nothing says which {self.serves} model to ask for. Set {variable.format('MODEL')} "
                             "to the name the server knows it by")
        self.name = "http"
        self.timeout_s = timeout_s
        self._key = key or settings.key(self.slot)
        try:
            self.extra = json.loads(settings.extra(self.slot) or "{}")
        except ValueError:
            self.extra = None
        if not isinstance(self.extra, dict):
            raise self.error(f"{variable.format('EXTRA')} must be a JSON object, such as {self.extra_example}")
        self._extra_variable = variable.format("EXTRA")

    #: What the extra setting looks like, for the message that asks for one.
    extra_example = '{"seed": 7}'

    def _send(self, body: dict[str, Any]) -> bytes:
        """What the server answered to `body`, as it came."""
        headers = {"Content-Type": "application/json"}
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        request = urllib.request.Request(  # noqa: S310 - the scheme was checked when this was made
            f"{self.url}/{self.path}", data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as answer:  # noqa: S310
                return answer.read()
        except urllib.error.HTTPError as error:
            with error:
                said = " ".join(error.read(600).decode("utf-8", "replace").split())[:300]
            raise self.error(f"the {self.serves} model at {self.url} refused with {error.code}"
                             + (f": {said}" if said else "")) from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise self.error(f"the {self.serves} model at {self.url} did not answer: "
                             f"{getattr(error, 'reason', None) or error}") from None

    def _ask(self, body: dict[str, Any]) -> Any:
        """What the server answered to `body`, read as JSON."""
        answer = self._send(body)
        try:
            return json.loads(answer)
        except ValueError:
            raise self.error(
                f"the {self.serves} model at {self.url} answered with something that is not JSON") from None
