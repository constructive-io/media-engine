"""A WebSocket (RFC 6455), on the standard library.

A conversation goes both ways at once: the microphone's sound goes up the
whole time while sentences come down, and either side may speak first. That
is a WebSocket, and a browser has one built in. This is the other end of it,
and enough of the browser's end to test it and to talk from a script:

    with websocket.connect("ws://127.0.0.1:8850/talk") as line:
        line.send('{"type": "text", "text": "Hello"}')
        answer = line.receive()

A message is a `str` or `bytes`, as it was sent. Nothing is compressed and
no extension is spoken.
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket as sockets
import struct
import threading
from typing import Any, BinaryIO
from urllib.parse import urlsplit

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
CONTINUED, TEXT, BINARY, CLOSE, PING, PONG = 0, 1, 2, 8, 9, 10
#: The largest message taken. A minute of a microphone is two megabytes.
LARGEST = 16 << 20


class Closed(Exception):
    """The other end has gone, or said something that is not a WebSocket."""


def accepting(key: str) -> str:
    """What a server answers a client's key with, to show it understood."""
    return base64.b64encode(hashlib.sha1((key + GUID).encode(), usedforsecurity=False).digest()).decode()


def _masked(data: bytes, mask: bytes) -> bytes:
    if not data:
        return data
    over = (mask * (len(data) // 4 + 1))[:len(data)]
    return (int.from_bytes(data, "big") ^ int.from_bytes(over, "big")).to_bytes(len(data), "big")


class Socket:
    """One end of a WebSocket that has already been agreed.

    `reader` and `writer` are the two directions of the connection. A client
    masks what it sends and a server does not, which is the one way the two
    ends differ.
    """

    def __init__(self, reader: BinaryIO, writer: BinaryIO, *, client: bool = False, closing: Any = None) -> None:
        self._reader, self._writer, self._client, self._closing = reader, writer, client, closing
        self._writing = threading.Lock()
        self._closed = False

    def send(self, message: str | bytes) -> None:
        """Send one message. A Closed when the other end has gone."""
        if isinstance(message, str):
            self._frame(TEXT, message.encode())
        else:
            self._frame(BINARY, bytes(message))

    def receive(self) -> str | bytes:
        """The next message. A Closed when there will be no more."""
        kind, held = None, b""
        while True:
            last, code, data = self._read()
            if code == PING:
                self._frame(PONG, data)
            elif code == PONG:
                pass
            elif code == CLOSE:
                self.close()
                raise Closed("the other end closed")
            elif (code == CONTINUED) == (kind is None):
                # A part of a message with no message begun, or a new one before the last was done.
                self.close(1002)
                raise Closed("the parts of a message came out of order")
            else:
                kind = kind if code == CONTINUED else code
                held += data
                if len(held) > LARGEST:
                    self.close(1009)
                    raise Closed("a message was too large to take")
                if last:
                    break
        if kind == BINARY:
            return held
        try:
            return held.decode()
        except UnicodeDecodeError:
            self.close(1007)
            raise Closed("a text message was not text") from None

    def close(self, code: int = 1000) -> None:
        """Say goodbye, once. It is no error for the other end to have gone already."""
        if self._closed:
            return
        try:
            self._frame(CLOSE, struct.pack("!H", code))
        except Closed:
            pass
        self._closed = True
        if self._closing is not None:
            self._closing()

    def __enter__(self) -> Socket:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _frame(self, code: int, data: bytes) -> None:
        head = bytes([0x80 | code])
        mask = 0x80 if self._client else 0
        if len(data) < 126:
            head += bytes([mask | len(data)])
        elif len(data) < 1 << 16:
            head += bytes([mask | 126]) + struct.pack("!H", len(data))
        else:
            head += bytes([mask | 127]) + struct.pack("!Q", len(data))
        if self._client:
            key = os.urandom(4)
            head, data = head + key, _masked(data, key)
        with self._writing:
            if self._closed:
                raise Closed("this end has closed")
            try:
                self._writer.write(head + data)
                self._writer.flush()
            except (OSError, ValueError) as error:
                raise Closed(f"the other end has gone: {error}") from None

    def _take(self, count: int) -> bytes:
        try:
            data = self._reader.read(count) if count else b""
        except (OSError, ValueError) as error:
            raise Closed(f"the other end has gone: {error}") from None
        if len(data) != count:
            raise Closed("the other end has gone")
        return data

    def _read(self) -> tuple[bool, int, bytes]:
        """One frame: whether it ends its message, what kind it is, and what is in it."""
        first, second = self._take(2)
        code, masked, length = first & 0x0F, bool(second & 0x80), second & 0x7F
        if first & 0x70 or code not in (CONTINUED, TEXT, BINARY, CLOSE, PING, PONG):
            self.close(1002)
            raise Closed("the other end spoke an extension that was not agreed")
        if masked == self._client:
            # A client must mask and a server must not: anything else is not a WebSocket's other end.
            self.close(1002)
            raise Closed("a frame was masked the wrong way round")
        if length == 126:
            (length,) = struct.unpack("!H", self._take(2))
        elif length == 127:
            (length,) = struct.unpack("!Q", self._take(8))
        if length > LARGEST or (code >= CLOSE and (length > 125 or not first & 0x80)):
            self.close(1009 if code < CLOSE else 1002)
            raise Closed("a frame was too large to take")
        mask = self._take(4) if masked else b""
        data = self._take(length)
        return bool(first & 0x80), code, _masked(data, mask) if masked else data


def connect(address: str, *, origin: str | None = None, timeout: float = 30) -> Socket:
    """Open a WebSocket to `address`, such as ws://127.0.0.1:8850/talk, as a client."""
    where = urlsplit(address)
    if where.scheme != "ws" or not where.hostname:
        raise Closed(f"{address}: an address here starts with ws://")
    port = where.port or 80
    try:
        made = sockets.create_connection((where.hostname, port), timeout=timeout)
    except OSError as error:
        raise Closed(f"{address} did not answer: {error}") from None
    reader, writer = made.makefile("rb"), made.makefile("wb")

    def closing() -> None:
        for held in (reader, writer, made):
            try:
                held.close()
            except OSError:
                pass

    key = base64.b64encode(os.urandom(16)).decode()
    asked = [f"GET {where.path or '/'} HTTP/1.1", f"Host: {where.hostname}:{port}", "Upgrade: websocket",
             "Connection: Upgrade", f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13"]
    if origin:
        asked.append(f"Origin: {origin}")
    try:
        writer.write(("\r\n".join(asked) + "\r\n\r\n").encode())
        writer.flush()
        status = reader.readline().decode("latin-1").strip()
        answered: dict[str, str] = {}
        while (line := reader.readline().decode("latin-1").strip()):
            name, _, value = line.partition(":")
            answered[name.strip().lower()] = value.strip()
    except OSError as error:
        closing()
        raise Closed(f"{address} did not answer: {error}") from None
    if status.split()[1:2] != ["101"] or answered.get("sec-websocket-accept") != accepting(key):
        closing()
        raise Closed(f"{address} is not a WebSocket: it answered {status or 'nothing'}")
    return Socket(reader, writer, client=True, closing=closing)
