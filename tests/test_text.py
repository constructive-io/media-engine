"""Text models: reading what one wrote, and the client for servers that speak the usual API.

No model runs here. The client is pointed at a small server on this machine
that writes down what it was sent and answers with what the test told it to.
"""

import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from constructive_media import adapters, text
from constructive_media.settings import Settings
from constructive_media.text import Embedder, TextError, Writer
from constructive_media.text.http import ChatServer, EmbeddingServer


class ReadTest(unittest.TestCase):
    def test_the_first_object_in_a_reply_is_what_it_said(self):
        self.assertEqual(text.read('{"substance": 0.8}'), {"substance": 0.8})
        self.assertEqual(text.read('Here you are:\n```json\n{"substance": 0.8, "topic": "the lamp"}\n```\nAnything else?'),
                         {"substance": 0.8, "topic": "the lamp"})
        self.assertEqual(text.read('{"a": 1} {"a": 2}'), {"a": 1})
        # A brace that opens nothing is stepped over.
        self.assertEqual(text.read('I weighed {it} up: {"a": 1}'), {"a": 1})

    def test_a_reply_with_no_object_in_it_says_nothing(self):
        for reply in (None, "", "I would rather not say.", "[0.8, 0.2]", '{"substance": ', "0.8"):
            with self.subTest(reply=reply):
                self.assertIsNone(text.read(reply))

    def test_a_number_is_a_share_however_it_was_written(self):
        self.assertEqual(text.share(0.25), 0.25)
        self.assertEqual(text.share(" 0.7 "), 0.7)
        self.assertEqual(text.share(1), 1.0)
        self.assertEqual(text.share(7), 1.0)
        self.assertEqual(text.share(-0.2), 0.0)
        for nothing in (None, "high", "", [], float("nan"), "nan"):
            with self.subTest(nothing=nothing):
                self.assertIsNone(text.share(nothing))


class Asked(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.server.asked.append((self.path, dict(self.headers), json.loads(self.rfile.read(length))))
        if self.server.hold is not None:
            self.server.hold.wait(5)
        status, body = self.server.answer
        body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *arguments):
        pass


class Served(unittest.TestCase):
    """A server on this machine that answers as it is told to."""

    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Asked)
        self.server.asked, self.server.answer, self.server.hold = [], (200, {}), None
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"

    def settings(self, **more):
        return Settings.from_env({
            "CONSTRUCTIVE_MEDIA_URL_TEXT": self.url + "/", "CONSTRUCTIVE_MEDIA_MODEL_TEXT": "made-up-reader",
            "CONSTRUCTIVE_MEDIA_URL_EMBEDDING": self.url, "CONSTRUCTIVE_MEDIA_MODEL_EMBEDDING": "made-up-embedder",
            **more,
        })

    def says(self, written):
        self.server.answer = (200, {"choices": [{"message": {"role": "assistant", "content": written}}]})

    def refused(self, call, *said):
        with self.assertRaises(TextError) as raised:
            call()
        for part in said:
            self.assertIn(part, str(raised.exception))
        return str(raised.exception)


class ChatServerTest(Served):
    def test_a_prompt_is_sent_as_one_message_and_the_reply_is_what_was_written(self):
        self.says("The lamp, mostly.")
        reader = ChatServer(settings=self.settings())
        self.assertIsInstance(reader, Writer)
        self.assertEqual((reader.name, reader.model, reader.url), ("http", "made-up-reader", self.url))
        self.assertEqual(reader.write("What is this about?", limit=40), "The lamp, mostly.")
        ((path, headers, sent),) = self.server.asked
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(sent, {"model": "made-up-reader", "messages": [{"role": "user", "content": "What is this about?"}],
                                "temperature": 0, "max_tokens": 40, "stream": False})
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertNotIn("Authorization", headers)

    def test_an_object_is_asked_for_when_one_is_wanted(self):
        self.says('{"substance": 0.9}')
        self.assertEqual(ChatServer(settings=self.settings()).write("Weigh it.", as_json=True), '{"substance": 0.9}')
        self.assertEqual(self.server.asked[0][2]["response_format"], {"type": "json_object"})
        self.assertEqual(self.server.asked[0][2]["max_tokens"], 200)

    def test_a_key_is_sent_and_never_shown(self):
        settings = self.settings(CONSTRUCTIVE_MEDIA_KEY_TEXT="made-up-key")
        self.assertNotIn("made-up-key", repr(settings))
        self.says("Yes.")
        reader = ChatServer(settings=settings)
        reader.write("Well?")
        self.assertEqual(self.server.asked[0][1]["Authorization"], "Bearer made-up-key")
        self.server.answer = (401, {"error": {"message": "that key is not one of ours"}})
        self.assertNotIn("made-up-key", self.refused(lambda: reader.write("Well?"), "refused with 401"))

    def test_what_is_given_outright_wins_over_the_settings(self):
        self.says("Yes.")
        reader = ChatServer("another-reader", self.url, "another-key", settings=Settings.from_env({}))
        reader.write("Well?")
        self.assertEqual(self.server.asked[0][2]["model"], "another-reader")
        self.assertEqual(self.server.asked[0][1]["Authorization"], "Bearer another-key")

    def test_a_reply_with_nothing_written_is_an_empty_one(self):
        self.says(None)
        self.assertEqual(ChatServer(settings=self.settings()).write("Well?"), "")

    def test_a_model_that_spent_everything_on_thinking_is_said_to_have(self):
        self.server.answer = (200, {"choices": [{"message": {"content": "", "reasoning": "Let me think about the lamp"},
                                                 "finish_reason": "length"}]})
        said = self.refused(lambda: ChatServer(settings=self.settings()).write("Well?", limit=40),
                            "made-up-reader used all 40 tokens it was allowed and answered nothing",
                            'Set CONSTRUCTIVE_MEDIA_EXTRA_TEXT to {"reasoning_effort": "none"} for a server '
                            'that takes that, or to {"max_tokens": 2000}')
        self.assertNotIn("Let me think", said)
        # Cut short with something written is an answer, to be read for what it holds.
        self.server.answer = (200, {"choices": [{"message": {"content": '{"substance": 0.'}, "finish_reason": "length"}]})
        self.assertEqual(ChatServer(settings=self.settings()).write("Well?"), '{"substance": 0.')

    def test_a_server_is_sent_what_else_it_was_set_to_be_sent(self):
        self.says("Yes.")
        extra = '{"reasoning_effort": "none", "temperature": 0.2, "max_tokens": 900, "model": "x", "stream": true}'
        ChatServer(settings=self.settings(CONSTRUCTIVE_MEDIA_EXTRA_TEXT=extra)).write("Well?", limit=40)
        self.assertEqual(self.server.asked[0][2], {
            "model": "made-up-reader", "messages": [{"role": "user", "content": "Well?"}], "stream": False,
            "reasoning_effort": "none", "temperature": 0.2, "max_tokens": 900})
        self.server.answer = (200, {"data": [{"index": 0, "embedding": [1.0]}]})
        EmbeddingServer(settings=self.settings(CONSTRUCTIVE_MEDIA_EXTRA_EMBEDDING='{"dimensions": 1024}')).embed(["a"])
        self.assertEqual(self.server.asked[1][2], {"model": "made-up-embedder", "input": ["a"], "dimensions": 1024})
        for odd in ("reasoning_effort=none", "[1]", '"none"', "{"):
            with self.subTest(odd=odd):
                self.refused(lambda: ChatServer(settings=self.settings(CONSTRUCTIVE_MEDIA_EXTRA_TEXT=odd)),
                             "CONSTRUCTIVE_MEDIA_EXTRA_TEXT must be a JSON object")

    def test_a_server_that_fails_is_said_plainly(self):
        reader = ChatServer(settings=self.settings())
        self.server.answer = (500, {"error": "the model fell\nover"})
        self.refused(lambda: reader.write("Well?"), f"the text model at {self.url} refused with 500",
                     '{"error": "the model fell\\nover"}')
        self.server.answer = (200, b"<html>not what was asked for</html>")
        self.refused(lambda: reader.write("Well?"), "answered with something that is not JSON")
        for odd in ({}, {"choices": []}, {"choices": [{}]}, ["choices"]):
            self.server.answer = (200, odd)
            with self.subTest(odd=odd):
                self.refused(lambda: reader.write("Well?"), "answered without a message in it")

    def test_a_server_that_is_not_there_or_does_not_answer_in_time(self):
        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            nowhere = f"http://127.0.0.1:{taken.getsockname()[1]}/v1"
        self.refused(lambda: ChatServer("made-up-reader", nowhere, settings=Settings.from_env({})).write("Well?"),
                     f"the text model at {nowhere} did not answer")
        self.server.hold = threading.Event()
        self.addCleanup(self.server.hold.set)
        slow = ChatServer(settings=self.settings(), timeout_s=0.2)
        self.refused(lambda: slow.write("Well?"), "did not answer")

    def test_nothing_is_sent_anywhere_it_was_not_told_to_go(self):
        nothing = Settings.from_env({})
        self.refused(lambda: ChatServer(settings=nothing), "nothing says where the text model is",
                     "CONSTRUCTIVE_MEDIA_URL_TEXT", "chat/completions")
        self.refused(lambda: EmbeddingServer(settings=nothing), "nothing says where the embedding model is",
                     "CONSTRUCTIVE_MEDIA_URL_EMBEDDING")
        self.refused(lambda: ChatServer(url=self.url, settings=nothing), "which text model",
                     "CONSTRUCTIVE_MEDIA_MODEL_TEXT")
        self.refused(lambda: EmbeddingServer(url=self.url, settings=nothing), "CONSTRUCTIVE_MEDIA_MODEL_EMBEDDING")
        for address in ("file:///etc/hosts", "localhost:8080/v1", "ftp://example.test/v1", "http:///v1"):
            with self.subTest(address=address):
                self.refused(lambda: ChatServer("made-up-reader", address, settings=nothing),
                             "CONSTRUCTIVE_MEDIA_URL_TEXT must be an http or https address")


class EmbeddingServerTest(Served):
    def test_each_text_comes_back_as_numbers_in_the_order_sent(self):
        # Answered out of order, which the API allows.
        self.server.answer = (200, {"data": [{"index": 1, "embedding": [0, 1]}, {"index": 0, "embedding": [1, 0.5]}]})
        reader = EmbeddingServer(settings=self.settings(CONSTRUCTIVE_MEDIA_KEY_EMBEDDING="made-up-key"))
        self.assertIsInstance(reader, Embedder)
        self.assertEqual((reader.name, reader.model), ("http", "made-up-embedder"))
        self.assertEqual(reader.embed(["the lamp", "the boats"]), [[1.0, 0.5], [0.0, 1.0]])
        ((path, headers, sent),) = self.server.asked
        self.assertEqual(path, "/v1/embeddings")
        self.assertEqual(sent, {"model": "made-up-embedder", "input": ["the lamp", "the boats"]})
        self.assertEqual(headers["Authorization"], "Bearer made-up-key")

    def test_nothing_is_asked_for_nothing(self):
        self.assertEqual(EmbeddingServer(settings=self.settings()).embed([]), [])
        self.assertEqual(self.server.asked, [])

    def test_an_answer_that_is_not_one_embedding_a_text_is_refused(self):
        reader = EmbeddingServer(settings=self.settings())
        for odd in ({}, {"data": [{"index": 0}]}, {"data": "numbers"}, {"data": [{"embedding": ["a"]}]}, [1, 2]):
            self.server.answer = (200, odd)
            with self.subTest(odd=odd):
                self.refused(lambda: reader.embed(["the lamp"]), "answered without embeddings in it")
        for short in ({"data": []}, {"data": [{"index": 0, "embedding": []}]},
                      {"data": [{"index": 0, "embedding": [1.0]}, {"index": 1, "embedding": [1.0]}]}):
            self.server.answer = (200, short)
            with self.subTest(short=short):
                self.refused(lambda: reader.embed(["the lamp"]), "was sent 1 texts")
        self.server.answer = (200, b'{"data": [{"index": 0, "embedding": [NaN]}]}')
        self.refused(lambda: reader.embed(["the lamp"]), "not all of them usable")
        self.server.answer = (503, b"")
        self.assertTrue(self.refused(lambda: reader.embed(["the lamp"])).endswith("refused with 503"))


class SlotsTest(unittest.TestCase):
    def test_the_client_is_what_fills_both_slots(self):
        for slot in ("text", "embedding"):
            described = adapters.registry().describe(slot)
            self.assertEqual((described.name, described.license, described.origin), ("http", "MIT", "builtin"))

    def test_a_slot_has_a_server_and_a_key_of_its_own(self):
        settings = Settings.from_env({
            "CONSTRUCTIVE_MEDIA_URL_TEXT": "http://reader.example.test/v1",
            "CONSTRUCTIVE_MEDIA_KEY_EMBEDDING": "made-up-key",
        })
        self.assertEqual((settings.url("text"), settings.key("text")), ("http://reader.example.test/v1", None))
        self.assertEqual((settings.url("embedding"), settings.key("embedding")), (None, "made-up-key"))
        self.assertIsNone(settings.extra("text"))
        self.assertEqual(Settings.from_env({"CONSTRUCTIVE_MEDIA_EXTRA_TEXT": '{"a": 1}'}).extra("text"), '{"a": 1}')
        self.assertEqual(dict(Settings.from_env({}).urls), {})


if __name__ == "__main__":
    unittest.main()
