"""Transcripts: each recording's speech, as segments and words in the database.

A segment is a sentence or so; a word is one word and the moment it was said.
Word times are the point: cutting on the transcript is guesswork without them.

A recording is transcribed once. Run again, the stage skips what it has done;
`force` replaces a transcript, and with it the passages drawn from the old one.

The vocabulary is the words a project uses that no general model will guess:
names, products, terms of art. A term with no project is listened for
everywhere; a term with a project only in that project's recordings.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .. import db
from ..settings import Settings
from ..transcription import Segment, Transcriber, TranscriptionError, clean, transcriber as default_transcriber


def terms(project: str | None = None) -> list[str]:
    """The vocabulary a recording in `project` is transcribed with."""
    cur = db.cursor()
    cur.execute(
        """SELECT term FROM media_video.vocabulary
            WHERE project IS NULL OR project = %s::text
            ORDER BY kind, term""",
        (project,),
    )
    return [term for (term,) in cur.fetchall()]


def learn(new: Iterable[str], project: str | None = None, kind: str | None = None) -> int:
    """Add terms to the vocabulary. Returns how many were not already there."""
    cur = db.cursor()
    added = 0
    for term in dict.fromkeys(term.strip() for term in new):
        if not term:
            continue
        cur.execute(
            """INSERT INTO media_video.vocabulary (project, term, kind)
               SELECT %s::text, %s::text, %s::text
                WHERE NOT EXISTS (SELECT 1 FROM media_video.vocabulary
                                   WHERE term = %s::text AND project IS NOT DISTINCT FROM %s::text)
               RETURNING id""",
            (project, term, kind, term, project),
        )
        added += len(cur.fetchall())
    return added


def transcribe(
    match: str | None = None,
    *,
    project: str | None = None,
    language: str = "en",
    force: bool = False,
    vocabulary: bool = True,
    source: str | Path | None = None,
    speaker: str | None = None,
    transcriber: Transcriber | None = None,
    settings: Settings | None = None,
    vocabulary_of: str | None = None,
) -> list[tuple[str, int]]:
    """Transcribe each recording that has sound. Returns (file name, segments written).

    The language is given, not detected: a recogniser that guesses the wrong
    language over a noisy opening does not write a worse transcript, it writes
    a different one. With `vocabulary` off the recogniser is told nothing
    about the project. `vocabulary_of` names the project whose terms are
    listened for, in place of the one each recording belongs to: for a
    recording filed under one project in which another's names are said.

    `source` is another file to listen to in place of the recording's own:
    the same sound cleaned up, say. Its times have to be the recording's, and
    it stands for one recording, so anything but one waiting is an error.
    `speaker` is written beside every line, for a microphone only one person
    wore.
    """
    cur = db.cursor()
    cur.execute(
        """SELECT id, path, coalesce(proxy_path, path), project
             FROM media.assets a
            WHERE medium IN ('video', 'audio') AND has_audio AND (%s = '' OR path ~* %s)
              AND (%s::text IS NULL OR project = %s)
              AND (%s OR NOT EXISTS (SELECT 1 FROM media_video.segments s WHERE s.asset_id = a.id))
            ORDER BY ingested_at, path""",
        (match or "", match or "", project, project, force),
    )
    waiting = cur.fetchall()
    if source is not None:
        if len(waiting) != 1:
            raise TranscriptionError(
                f"another file stands for one recording, and {len(waiting)} are waiting to be transcribed"
                + ("" if waiting else "; --force transcribes one again"))
        if not Path(source).is_file():
            raise TranscriptionError(f"there is no file at {source}")
    if waiting and transcriber is None:
        transcriber = default_transcriber(settings=settings)
    done = []
    for asset_id, path, its_own, its_project in waiting:
        prompt = ", ".join(terms(vocabulary_of or its_project)) if vocabulary else ""
        heard = clean(transcriber.transcribe(Path(source or its_own), language=language, prompt=prompt or None),
                      prompt or None)
        if force:
            rewrite(asset_id, heard, transcriber.model, speaker)
        else:
            _write(cur, asset_id, heard, transcriber.model, speaker)
        done.append((Path(path).name, len(heard)))
    return done


def rewrite(asset_id: Any, heard: list[Segment], model: str, speaker: str | None = None) -> None:
    """Replace a recording's transcript with this one, all of it or none."""
    with db.transaction():
        cur = db.cursor()
        _forget(cur, asset_id)
        _write(cur, asset_id, heard, model, speaker)


def _forget(cur: Any, asset_id: Any) -> None:
    """Remove a recording's transcript and what was drawn from it.

    Passages cut from the old words would sit beside those cut from the new,
    both looking current. They are cheap to propose again, so they go.
    """
    cur.execute("DELETE FROM media_video.segments WHERE asset_id = %s", (asset_id,))
    cur.execute(
        """DELETE FROM media_video.passages p USING media.take_assets ta
            WHERE ta.take_id = p.take_id AND ta.asset_id = %s""",
        (asset_id,),
    )


def _write(cur: Any, asset_id: Any, heard: list[Segment], model: str, speaker: str | None = None) -> None:
    """Store a transcript in one statement, so a recording has all of it or none."""
    if not heard:
        return
    segments = [
        {"idx": index, "start_s": segment.start, "end_s": segment.end, "text": segment.text,
         "avg_logprob": segment.avg_logprob, "no_speech_prob": segment.no_speech_prob,
         "compression_ratio": segment.compression_ratio}
        for index, segment in enumerate(heard)
    ]
    words = [
        {"segment": index, "idx": position, "start_s": word.start, "end_s": word.end, "word": word.text}
        for index, segment in enumerate(heard)
        for position, word in enumerate(segment.words)
    ]
    cur.execute(
        """WITH written AS (
             INSERT INTO media_video.segments
               (asset_id, idx, start_s, end_s, text, text_asr,
                avg_logprob, no_speech_prob, compression_ratio, model, speaker)
             SELECT %s, s.idx, s.start_s, s.end_s, s.text, s.text,
                    s.avg_logprob, s.no_speech_prob, s.compression_ratio, %s, %s::text
               FROM jsonb_to_recordset(%s) AS s(
                    idx integer, start_s double precision, end_s double precision, text text,
                    avg_logprob double precision, no_speech_prob double precision,
                    compression_ratio double precision)
             RETURNING id, idx
           )
           INSERT INTO media_video.words (segment_id, idx, start_s, end_s, word)
           SELECT written.id, w.idx, w.start_s, w.end_s, w.word
             FROM jsonb_to_recordset(%s) AS w(
                  segment integer, idx integer, start_s double precision,
                  end_s double precision, word text)
             JOIN written ON written.idx = w.segment""",
        (asset_id, model, speaker, db.jsonb(segments), db.jsonb(words)),
    )
