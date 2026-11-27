"""Pictures and video made from a description, and a record of every one.

A `Maker` is given words and writes a picture or a piece of video. The
engine asks for one by slot, `image` or `video`, and never knows which model
or which program made it.

What is made is kept apart from what was filmed, and that is most of what
this module is for:

- **Every request is a row**, with the words, the model, the settings and the
  seed, whether it worked or not. A picture nobody can account for is the one
  that ends up in a cut by accident.
- **The same request is made once.** Asked again for the same words with the
  same settings, the engine hands back the file it has. Models cost minutes
  or money; a script that is run twice should cost neither. `again` asks for
  another, with a seed of its own.
- **A made file is on record as generated, never as a source.** It is a
  derived file of the kind `generated`, with the picture it started from as
  what it was derived from, when there was one. So a cut can always be asked
  which of its clips nobody filmed.
- **Some makers answer later.** One that runs on this machine writes the file
  before it returns. One that asks a service is handed the request, and
  `collect` brings the file in when the service has made it.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .. import adapters, db, derive, ffmpeg
from ..ingest import quick_hash
from ..settings import Settings

#: What can be made, and what a file of it ends in. Each is also the slot that makes it.
MEDIUMS = {"image": ".png", "video": ".mp4"}
#: The folder under the work folder that made files go to.
FOLDER = "generated"
#: What lineage says made them.
TOOL = "media generate"
KIND = "generated"

#: How a request came out.
MADE, HAD, WAITING, WOULD = "made", "had", "waiting", "would"
#: Where a request stands, as its row says it.
STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")


class GenerateError(RuntimeError):
    """Nothing could be made, or there is nothing to make it with."""


@dataclass(frozen=True)
class Request:
    """What to make. Whatever is left unset is the maker's to choose."""

    medium: str
    prompt: str
    width: int | None = None
    height: int | None = None
    #: How long a piece of video is to run.
    seconds: float | None = None
    #: How many passes the model makes: more is slower, and up to a point better.
    steps: int | None = None
    #: The same request with the same seed is the same picture.
    seed: int | None = None
    #: A picture to start from.
    image: Path | None = None


@dataclass(frozen=True)
class Job:
    """Where a request stands with a maker that answers later."""

    state: str  # "running", "succeeded" or "failed"
    error: str | None = None


@runtime_checkable
class Maker(Protocol):
    """Something that makes a picture or a piece of video from words.

    One that knows what a request will cost may also have
    `price(request) -> float | None`, in US dollars. One that can tell a
    request it cannot make without trying may have `check(request)`, which
    raises `GenerateError`; it is asked before anything is written or run.
    """

    name: str
    #: What is recorded beside what it made.
    model: str

    def make(self, request: Request, target: Path) -> Path:
        """Write what `request` asks for to `target`, and return where it was written."""
        ...


@runtime_checkable
class Later(Protocol):
    """A maker that answers later: a service that is asked, and then asked whether it has finished."""

    name: str
    model: str

    def submit(self, request: Request) -> str:
        """Ask for it. Returns what the service knows the request by."""
        ...

    def poll(self, job: str) -> Job:
        """Where the request stands."""
        ...

    def fetch(self, job: str, target: Path) -> Path:
        """Write what was made to `target`."""
        ...


def maker_for(medium: str, name: str | None = None, *, settings: Settings | None = None) -> Any:
    """The chosen maker for a medium, or the configured default."""
    if medium not in MEDIUMS:
        raise GenerateError(f"unknown medium {medium!r}; what can be made is: {', '.join(MEDIUMS)}")
    return adapters.get(medium, name, settings=settings)


@dataclass(frozen=True)
class Generation:
    """One request, as it is on record."""

    id: Any
    medium: str
    model: str
    backend: str
    prompt: str
    params: Mapping[str, Any]
    status: str
    #: What a maker that answers later knows it by.
    job: str | None = None
    cost_usd: float | None = None
    error: str | None = None
    created_at: datetime | None = None
    #: The file, where there is one or is to be one.
    file: Path | None = None
    input_asset_id: Any = None
    asset_id: Any = None


@dataclass(frozen=True)
class Made:
    """How one request came out: `made`, `had` already, `waiting` on a service, or `would` be made."""

    file: Path
    state: str
    generation: Generation | None = None
    price: float | None = None
    #: The model and the maker, for a request that was only looked at.
    model: str = ""
    backend: str = ""


@dataclass(frozen=True)
class Collected:
    """What asking the services again found."""

    made: list[Generation] = field(default_factory=list)
    failed: list[Generation] = field(default_factory=list)
    waiting: list[Generation] = field(default_factory=list)
    #: Asked of a maker that is not installed here any more.
    lost: list[Generation] = field(default_factory=list)


@dataclass(frozen=True)
class Spend:
    """What one model has been asked for, by how it came out."""

    model: str
    backend: str
    status: str
    runs: int
    usd: float
    seconds: float


@dataclass(frozen=True)
class Synthetic:
    """One clip of a cut that nobody filmed."""

    timeline: str
    #: The clip's number in its cut, from 1.
    number: int
    file: str
    model: str
    prompt: str
    #: `picture` or `sound`: which of the clip's two it is.
    track: str


def request_key(model: str, request: Request, image: str | None = None) -> str:
    """What makes two requests the same one: the model, the words and every setting.

    `image` is what identifies the picture a request starts from. Spaces in
    the words do not count: a prompt wrapped over two lines is the same one.
    """
    said = {
        "model": model, "medium": request.medium, "prompt": " ".join(request.prompt.split()),
        "width": request.width, "height": request.height, "seconds": request.seconds,
        "steps": request.steps, "seed": request.seed, "image": image,
    }
    return hashlib.sha256(json.dumps(said, sort_keys=True).encode()).hexdigest()[:32]


def make(
    request: Request, *, maker: Any = None, adapter: str | None = None, out: str | Path | None = None,
    project: str | None = None, again: bool = False, max_usd: float | None = None, dry_run: bool = False,
    settings: Settings | None = None,
) -> Made:
    """Make what `request` asks for, or hand back what was made of it before.

    `adapter` names the maker, in place of the configured one; `maker` is one
    already built. `out` is where the file goes, in place of the work folder.
    `again` makes another: with a new seed when none was given, and in place
    of the first when one was. `max_usd` refuses a request its maker prices
    above it. A dry run writes nothing and makes nothing.
    """
    settings = settings or Settings.from_env()
    if request.medium not in MEDIUMS:
        raise GenerateError(f"unknown medium {request.medium!r}; what can be made is: {', '.join(MEDIUMS)}")
    prompt = " ".join(request.prompt.split())
    if not prompt:
        raise GenerateError("there are no words to make anything from")
    for name in ("width", "height", "seconds", "steps"):
        value = getattr(request, name)
        if value is not None and value <= 0:
            raise GenerateError(f"{name} is {value:g}; it should be more than nothing")
    if request.seconds is not None and request.medium != "video":
        raise GenerateError("only video runs for a length of time")
    image = image_hash = input_asset = None
    if request.image is not None:
        image = Path(request.image).expanduser().resolve()
        if not image.is_file():
            raise GenerateError(f"the picture to start from is not there: {image}")
        image_hash = quick_hash(image)
        input_asset = _asset_at(image, image_hash)
    maker = maker or maker_for(request.medium, adapter, settings=settings)
    seed = request.seed
    if again and seed is None:
        seed = secrets.randbelow(2 ** 31)
    asked = replace(request, prompt=prompt, seed=seed, image=image)
    check = getattr(maker, "check", None)
    if check is not None:
        check(asked)
    key = request_key(maker.model, asked, image_hash)
    target = _target(out, asked, key, settings)
    price = _price(maker, asked)
    before = _one("g.request_key = %s", key)
    looked = {"price": price, "model": maker.model, "backend": maker.name}
    if before is not None and not again:
        if before.status == "succeeded" and before.file is not None and before.file.is_file():
            return Made(before.file, HAD, before, **looked)
        if before.status in ("queued", "running") and before.job:
            return Made(before.file or target, WAITING, before, **looked)
    if max_usd is not None and price is not None and price > max_usd:
        raise GenerateError(f"{maker.name} prices this at ${price:.2f}, over the ${max_usd:.2f} allowed")
    _free(target, key)
    if dry_run:
        return Made(target, WOULD, before, **looked)

    params = {
        name: value for name, value in (
            ("width", asked.width), ("height", asked.height), ("seconds", asked.seconds),
            ("steps", asked.steps), ("seed", asked.seed), ("file", str(target)),
            ("from", None if image is None else str(image)), ("project", project),
        ) if value is not None
    }
    cur = db.cursor()
    cur.execute(
        """INSERT INTO media_video.generations
             (medium, model, backend, prompt, params, input_asset_id, request_key, status, cost_usd,
              created_at, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, 'running', %s, clock_timestamp(), clock_timestamp())
           ON CONFLICT (request_key) DO UPDATE
             SET backend = EXCLUDED.backend, params = EXCLUDED.params, status = 'running',
                 input_asset_id = EXCLUDED.input_asset_id, provider_job_id = NULL, output_asset_id = NULL,
                 cost_usd = EXCLUDED.cost_usd, error = NULL, updated_at = clock_timestamp()
           RETURNING id""",
        (asked.medium, maker.model, maker.name, prompt, db.jsonb(params), input_asset, key, price),
    )
    generation = cur.fetchone()[0]

    if hasattr(maker, "submit"):
        try:
            job = str(maker.submit(asked))
        except BaseException as error:
            _failed(generation, error)
            raise
        cur.execute(
            """UPDATE media_video.generations SET status = 'queued', provider_job_id = %s,
                      updated_at = clock_timestamp() WHERE id = %s""",
            (job, generation),
        )
        return Made(target, WAITING, _one("g.id = %s", generation), **looked)

    began = time.monotonic()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        made = Path(maker.make(asked, target))
        if not made.is_file():
            raise GenerateError(f"{maker.name} said it made {made.name}, and there is no such file")
        _succeeded(_one("g.id = %s", generation), made, took_s=time.monotonic() - began)
    except BaseException as error:
        _failed(generation, error)
        raise
    return Made(made, MADE, _one("g.id = %s", generation), **looked)


def collect(*, maker: Any = None, settings: Settings | None = None) -> Collected:
    """Ask every service that was handed a request whether it has finished, and bring in what it made.

    `maker` is one already built, asked in place of the installed one of its name.
    """
    settings = settings or Settings.from_env()
    found = Collected()
    for one in _many("g.status IN ('queued', 'running') AND g.provider_job_id IS NOT NULL", (),
                     order="g.created_at, g.id"):
        if maker is not None and maker.name == one.backend:
            asked_of = maker
        else:
            try:
                asked_of = adapters.get(one.medium, one.backend, settings=settings)
            except adapters.AdapterError:
                found.lost.append(one)
                continue
        if not hasattr(asked_of, "poll"):
            found.lost.append(one)
            continue
        job = asked_of.poll(one.job)
        if job.state == "failed":
            _failed(one.id, job.error or f"{one.backend} gave no reason")
            found.failed.append(_one("g.id = %s", one.id))
        elif job.state == "succeeded":
            target = one.file or _target(None, Request(one.medium, one.prompt), str(one.id).replace("-", ""), settings)
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                made = Path(asked_of.fetch(one.job, target))
                if not made.is_file():
                    raise GenerateError(f"{one.backend} said it sent {made.name}, and there is no such file")
                _succeeded(one, made)
            except Exception as error:  # noqa: BLE001 - one that cannot be fetched must not stop the rest
                _failed(one.id, error)
                found.failed.append(_one("g.id = %s", one.id))
                continue
            found.made.append(_one("g.id = %s", one.id))
        else:
            if one.status != "running":
                db.cursor().execute(
                    "UPDATE media_video.generations SET status = 'running', updated_at = clock_timestamp() WHERE id = %s",
                    (one.id,),
                )
            found.waiting.append(one)
    return found


def listed(medium: str | None = None, status: str | None = None, *, limit: int | None = None) -> list[Generation]:
    """What has been asked for, the latest first."""
    if medium is not None and medium not in MEDIUMS:
        raise GenerateError(f"unknown medium {medium!r}; what can be made is: {', '.join(MEDIUMS)}")
    if status is not None and status not in STATUSES:
        raise GenerateError(f"unknown status {status!r}; a request is one of: {', '.join(STATUSES)}")
    return _many(
        "(%s::text IS NULL OR g.medium = %s) AND (%s::text IS NULL OR g.status = %s)",
        (medium, medium, status, status), order="g.created_at DESC, g.id", limit=limit,
    )


def spend() -> list[Spend]:
    """What each model has been asked for, and what it came to."""
    cur = db.cursor()
    cur.execute(
        """SELECT model, backend, status, runs, usd, seconds FROM media_video.v_generation_spend
            ORDER BY model, backend, status"""
    )
    return [Spend(model, backend, status, int(runs), float(usd or 0), float(seconds or 0))
            for model, backend, status, runs, usd, seconds in cur.fetchall()]


def synthetic(timeline: str | None = None) -> list[Synthetic]:
    """The clips nobody filmed: in one cut, or in every cut."""
    cur = db.cursor()
    cur.execute(
        """SELECT timeline, idx, file, model, prompt, track FROM media_video.v_synthetic_in_timeline
            WHERE %s::text IS NULL OR timeline = %s
            ORDER BY timeline, idx, track""",
        (timeline, timeline),
    )
    return [Synthetic(name, int(idx), file, model, prompt, track)
            for name, idx, file, model, prompt, track in cur.fetchall()]


# Rows.

_READ = """SELECT g.id, g.medium, g.model, g.backend, g.prompt, g.params, g.status, g.provider_job_id,
                  g.cost_usd, g.error, g.created_at, a.path, g.input_asset_id, g.output_asset_id
             FROM media_video.generations g
             LEFT JOIN media.assets a ON a.id = g.output_asset_id"""


def _many(where: str, values: tuple[Any, ...], *, order: str, limit: int | None = None) -> list[Generation]:
    cur = db.cursor()
    cur.execute(f"{_READ} WHERE {where} ORDER BY {order}" + (" LIMIT %s::int" if limit else ""),
                (*values, limit) if limit else values)
    found = []
    for (id_, medium, model, backend, prompt, params, status, job, cost, error, created_at, path,
         input_asset, asset) in cur.fetchall():
        params = params or {}
        file = path or params.get("file")
        found.append(Generation(
            id_, medium, model, backend, prompt, params, status, job, None if cost is None else float(cost),
            error, created_at, None if file is None else Path(file), input_asset, asset,
        ))
    return found


def _one(where: str, value: Any) -> Generation | None:
    found = _many(where, (value,), order="g.created_at")
    return found[0] if found else None


def _asset_at(file: Path, content_hash: str) -> Any:
    """The asset a file is on record as, by where it is or else by what is in it."""
    cur = db.cursor()
    cur.execute(
        """SELECT id FROM media.assets WHERE path = %s OR content_hash = %s
            ORDER BY path = %s DESC LIMIT 1""",
        (str(file), content_hash, str(file)),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _price(maker: Any, request: Request) -> float | None:
    price = getattr(maker, "price", None)
    if price is None:
        return None
    value = price(request)
    return None if value is None else float(value)


def _target(out: str | Path | None, request: Request, key: str, settings: Settings) -> Path:
    """Where a request's file goes: where it was told to, or the work folder, named for its words."""
    suffix = MEDIUMS[request.medium]
    if out is not None:
        target = Path(out).expanduser().resolve()
        if target.suffix.lower() != suffix:
            raise GenerateError(f"{request.medium} is written as a {suffix} file, and {target.name} is not one")
        return target
    words = re.findall(r"[a-z0-9]+", request.prompt.lower())[:6]
    name = "-".join(words)[:40].strip("-") or request.medium
    return (settings.work_dir / FOLDER / f"{name}-{key[:8]}{suffix}").resolve()


def _free(target: Path, key: str) -> None:
    """Refuse to write over a file that is not this request's own."""
    cur = db.cursor()
    cur.execute(
        """SELECT g.request_key = %s, g.status FROM media_video.generations g
             LEFT JOIN media.assets a ON a.id = g.output_asset_id
            WHERE a.path = %s OR g.params->>'file' = %s""",
        (key, str(target), str(target)),
    )
    named = cur.fetchall()
    if any(not own and status in ("queued", "running", "succeeded") for own, status in named):
        raise GenerateError(f"{target} is what another request made; give this one somewhere else to go")
    if not named and target.exists():
        raise GenerateError(f"{target} is there already, and nothing made here put it there; "
                            "give this one somewhere else to go")


def _failed(generation: Any, error: Any) -> None:
    said = "stopped before it had finished" if isinstance(error, KeyboardInterrupt) else str(error)
    db.cursor().execute(
        """UPDATE media_video.generations SET status = 'failed', error = %s, updated_at = clock_timestamp()
            WHERE id = %s""",
        (said[-2000:] or type(error).__name__, generation),
    )


def _succeeded(one: Generation, file: Path, *, took_s: float | None = None) -> None:
    """Put a made file on record: an asset, what it came from, and the request as done."""
    facts: dict[str, Any] = {"file": str(file.resolve())}
    if took_s is not None:
        facts["took_s"] = round(took_s, 1)
    if one.medium == "video":
        described = ffmpeg.probe(file)
        if described is None or not described.duration_s:
            raise GenerateError(f"{file.name} was written, and it is not video that plays")
        facts["duration_s"] = round(described.duration_s, 3)
    told = {name: value for name, value in one.params.items() if name not in ("file", "project")}
    with db.transaction():
        asset = derive.record(
            file, KIND, TOOL,
            [derive.Source(one.input_asset_id, "reference")] if one.input_asset_id is not None else (),
            params={"medium": one.medium, "model": one.model, "adapter": one.backend, "prompt": one.prompt,
                    **told, "generation": str(one.id)},
            project=one.params.get("project"),
        )
        db.cursor().execute(
            """UPDATE media_video.generations
                  SET status = 'succeeded', output_asset_id = %s, error = NULL,
                      params = params || %s::jsonb, updated_at = clock_timestamp()
                WHERE id = %s""",
            (asset, json.dumps(facts), one.id),
        )


__all__ = [
    "Collected", "GenerateError", "Generation", "Job", "Later", "Made", "Maker", "Request", "Spend",
    "Synthetic", "collect", "listed", "make", "maker_for", "request_key", "spend", "synthetic",
]
