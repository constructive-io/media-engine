"""Model files: fetched once, checked, kept.

No model ships with this repository. An adapter that needs one names where it
is published and what its bytes must hash to; `fetch` downloads it into the
models folder the first time and refuses anything that does not match.
"""

from __future__ import annotations

import hashlib
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

CHUNK = 1 << 20
TIMEOUT_S = 60


class ModelError(RuntimeError):
    """A model file could not be fetched, or is not the file that was asked for."""


@dataclass(frozen=True)
class Remote:
    """One published file: where it is and the SHA-256 its bytes must have."""

    url: str
    sha256: str


def fetch(remote: Remote, target: str | Path) -> Path:
    """`target`, downloaded from `remote` if it is not there yet.

    The download is written beside the target and only moved into place once
    its hash matches, so a failed or tampered download never looks like a model.
    A file that is already there is used as it is.
    """
    target = Path(target)
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    scratch = target.with_name(f".{target.name}.part")
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(remote.url, timeout=TIMEOUT_S) as response, open(scratch, "wb") as out:
            while chunk := response.read(CHUNK):
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != remote.sha256:
            raise ModelError(
                f"{remote.url} is not the file that was expected "
                f"(sha256 {digest.hexdigest()}, wanted {remote.sha256})"
            )
        os.replace(scratch, target)
    except (urllib.error.URLError, OSError) as error:
        raise ModelError(
            f"could not download {remote.url}: {error}. "
            f"Fetch it yourself and save it as {target}."
        ) from error
    finally:
        scratch.unlink(missing_ok=True)
    return target
