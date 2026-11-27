# Talking with a model

`media talk` serves a page you can talk to. What you say is written down by
the [`transcription`](./ADAPTERS.md#transcription) slot, answered by the
`text` slot and said back by the [`speech`](./ADAPTERS.md#speech) slot. The
answer is said a sentence at a time, so the first sentence is heard while the
rest is still being written.

```sh
media talk                      # prints an address; open it, press Microphone, and speak
media talk --voice ada          # answer in a persona's voice, as `media voice` stored it
media talk --profile NAME       # answer in a voice the speech adapter knows by name, with no database
media talk --voice ada --quick af_heart   # said in a quick voice, and again in a cloned one: needs a plugin
media talk --typed              # listen to nothing: turns are typed, and still answered aloud
media talk --system "You are a harbour pilot. Answer in a sentence."
media talk --measure            # serve nothing: time each model, and the wait they add up to
```

It uses the three models you have already set. Nothing else is installed: the
server, the page and the WebSocket between them are the engine's own, written
on Python's standard library.

## What you need

| Slot | For | Without it |
| --- | --- | --- |
| `text` | the answer. A server that speaks the usual chat API, set with `CONSTRUCTIVE_MEDIA_URL_TEXT` and `CONSTRUCTIVE_MEDIA_MODEL_TEXT` | nothing to talk to |
| `speech` | the answer aloud | nothing is heard |
| `transcription` | what you said | `--typed`: turns are typed |

A small text model is the right one here. The wait is what a conversation
feels like, and a model that thinks before it answers spends the wait
thinking. `CONSTRUCTIVE_MEDIA_EXTRA_TEXT` carries whatever a server needs to
be told to stop a model doing so.

## Knowing when you are speaking

The page sends the microphone's sound the whole time it is open, and most of
that is a room. Two things can tell speech from a room:

- **Silero VAD**, a small model made for it, when NumPy and ONNX Runtime are
  installed (`pip install "constructive-media[speakers]"`). It knows a voice
  from a door, a fan or a keyboard. The model is two megabytes, is fetched the
  first time, and is listed in [MODELS.md](./MODELS.md#transcription-speech-to-text).
- **Loudness**, when they are not: speech is whatever is well above how loud
  the room has been. It needs nothing installed, and is enough in a quiet room
  with a microphone that is close.

`media talk` says which it is using when it starts.

A sentence is taken to have ended after six tenths of a second of quiet.
Something said for longer than thirty seconds is written down in pieces, and
answered once, at the end.

## Speaking over it

Speak while it is answering and it stops: what was being said is cut off,
what was still being written is let go, and what you said is answered. The
Stop button does the same, and so does typing a turn. The conversation
remembers what it got as far as saying, not what it would have said.

The microphone hears the answer as well as you. The page asks the browser to
take the answer back out of what the microphone hears, and browsers mostly do.
Where one does not, the answer interrupts itself: wear headphones.

## The wait

```
$ media talk --measure
             adapter      model                              first time      warm
  hearing    whispercpp   large-v3-turbo                        1069 ms    847 ms
             heard: The tide comes in twice a day, and it does not wait for anybody.
  answering  http         a-small-model                         1488 ms    100 ms
             its first word came in 21 ms
  saying     http         a-voice                                464 ms    436 ms
from the end of your sentence to the first sound: 1383 ms, once warm
```

Each model is timed where it runs, twice: the first time, when it may still
have to be loaded, and again once warm. What is counted of the answer is its
first sentence, since that is what is waited for. `--measure RECORDING` times
the hearing on a recording of your own, and `--json` prints all of it to be
read by a program. The status is 1 when a part failed.

Those numbers are from one desktop, with every model on it. Of the three, the
hearing is usually the longest: a smaller Whisper
(`CONSTRUCTIVE_MEDIA_MODEL_TRANSCRIPTION=small.en`) is the first thing to try.

The saying is the other one to look at. A model that clones took 436
thousandths of a second over that first sentence. The
[`kokoro` adapter](./ADAPTERS.md#a-small-quick-model) runs a small model that
took 159 on the same desktop, in a voice of its own and not a cloned one
([a quick voice](./VOICES.md#a-quick-voice)).

With a [voice conversion plugin](./ADAPTERS.md#voice-conversion) installed,
`--quick` puts the two together: the answer is said in a quick voice and said
again in a cloned one.

```
$ media talk --measure --voice ada --quick af_heart
  saying     kokoro+seedvc mlx-community/Kokoro-82M-bf16 + Plachta/Seed-VC    5161 ms    699 ms
from the end of your sentence to the first sound: 1796 ms, once warm
```

That was 0.7 to 1.0 seconds over a first sentence on the same desktop,
depending on the sentence, and 0.6 to 0.9 seconds more to wait than the quick
voice alone ([in a conversation](./VOICES.md#in-a-conversation)).

When it is served, `media talk` says a word and listens to it before it prints
the address. A voice that cannot speak or a model that cannot listen is found
out then, and the slow first use of each is not your first sentence. The page
shows the same times under each turn.

## Who can reach it

It listens on this machine only (`127.0.0.1`), on port 8850 unless `--port`
says another, and it answers only the page it served itself. A page from
anywhere else that is open in the same browser is refused, and so is a name
that was made to point at this machine. To talk to it from another machine,
forward the port over SSH; a browser gives a page a microphone only on
`localhost` or over HTTPS.

Nothing is kept. No turn, no sound and no answer is written to the database
or to a file: a conversation is gone when its page is closed.

## From a program

The page is one client of a WebSocket at `/talk`, and anything can be another.
Up it go 16-bit samples, one channel, 16,000 a second, as binary messages, and

```json
{"type": "text", "text": "What is the tide doing?"}
{"type": "hush"}
```

Down it come the conversation's events, and each sentence's sound as a binary
message holding a WAV file:

| Message | When |
| --- | --- |
| `{"type": "ready", "hears": true, "text": …, "speech": …, "transcription": …}` | once, first |
| `{"type": "state", "value": "listening"}` | it changes: `listening`, `thinking`, `speaking` |
| `{"type": "heard", "text": …}` | a turn was written down, or typed |
| `{"type": "said", "text": …}` | a sentence of the answer, just before its sound |
| `{"type": "hush"}` | the answer was stopped: drop any sound not yet played |
| `{"type": "timing", "heard_ms": …, "first_word_ms": …, "first_sentence_ms": …, "first_sound_ms": …}` | the answer has all been sent: how long the turn waited for each thing, in milliseconds |
| `{"type": "trouble", "text": …}` | a model failed; the conversation goes on |
| `{"type": "done"}` | the answer is finished |

In Python the same conversation needs no server:

```python
from constructive_media import speech, talk, text, transcription

parts = talk.Parts(writer=text.writer(), synthesizer=speech.synthesizer(),
                   voice=speech.Voice("voice"), transcriber=transcription.transcriber())
conversation = talk.Conversation(parts, tell=print)   # tell is handed each event, and each WAV as bytes
conversation.typed("What is the tide doing?")
conversation.wait()
```

## A text adapter that answers as it goes

A `text` adapter is asked for `write(prompt)`. One that also has
`reply(messages, *, limit)`, giving the answer in pieces as they are written,
is what makes the first sentence early: the built-in `http` adapter has it,
and reads a server's stream. An adapter without it still works. It is handed
the conversation as one prompt, and its whole answer arrives at once.
