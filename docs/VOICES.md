# Voices

A voice belongs to a persona and has an emotion. It is a row in the database
that says which adapter speaks it and what that adapter knows it by. There are
two kinds:

- **A voice the speech server has of its own.** The row holds the server's
  name for it, and that is all.
- **A voice cloned from a clip.** The row also holds where a short recording
  of the voice is, and the words said in it. The clip is sent to a server that
  clones, or handed to an adapter that does.

No voice and no recording is kept in this repository. Voices that are meant to
be shared are kept in a [library](./LIBRARIES.md), a repository of their own.

**Clone only a voice you have the right to: your own, or one whose owner said
yes to this use.** A library records that for every voice, and one that does
not is refused.

## Say something

```sh
me say --voice ada "The lantern is lit at dusk." out/line.wav
me say --voice ada "Quietly, now." out/hush.wav --emotion whisper --seed 31
```

The words are said, listened back to, and said again with another seed until a
take passes, as every line of a script is. It prints the seed that passed, so
the same take can be had again.

## Make a clip to clone from

What clones best, measured over many voices and models:

| | |
| --- | --- |
| Length | 15 to 28 seconds. Under 12 there is too little of the voice; over 30 most models stop listening |
| Who | one person, in one mood, with nobody else and no music |
| Ending | a plain, complete sentence. A clip that ends mid-word leaks its last words into what is said |
| Form | one channel, 24 kHz, at -19 LUFS with peaks under -2 dB |
| Words | exactly what is said in the finished clip, not in the recording it was cut from |

`me reference` does the last three, and tells you about the first two:

```sh
me reference recording.wav                               # measure it, and say what is wrong with it
me reference recording.wav voices/ada/neutral.flac --from 12.5 --to 34 --listen
```

The second cuts the part asked for, takes out the rumble under 70 Hz and the
silence at either end, brings it to the loudness and peak above, and writes
the clip. With `--listen` the finished clip is transcribed and its words are
written beside it as `voices/ada/neutral.txt`; read them against the clip and
put right what was misheard. `--said "…"` gives the words instead.

Keep the recording the clip was cut from somewhere else. A clip is a few
hundred kilobytes; a library of clips stays small enough to clone in a moment.

## Give it to a persona

```sh
me voice ada the-servers-name-for-a-voice --new                       # a voice the server has
me voice ada ada --reference voices/ada/neutral.flac --new            # a voice cloned from a clip
me voice ada ada-hushed --reference voices/ada/whisper.flac --emotion whisper
me voices
```

The words of a clip are read from the `.txt` beside it, or given with
`--said`.

## Send the clip to a server that clones

Servers that clone do not agree on what the clip is called in a request, so
you say, once, in the extra fields sent with every request:

```sh
export CONSTRUCTIVE_MEDIA_EXTRA_SPEECH='{"voice": null, "ref_audio": "{reference:base64}", "ref_text": "{said}"}'
```

| Written | Becomes |
| --- | --- |
| `{reference}` | the path of the voice's clip, for a server on this machine |
| `{reference:base64}` | the clip itself, as base64 |
| `{said}` | the words said in it |
| `{persona}`, `{emotion}` | whose voice it is, and which of theirs |

A field that names something the voice does not have is left out of the
request, so one setting serves cloned voices and the server's own. A field set
to `null` is not sent: `"voice": null` is for a server that refuses a name it
does not know. The field names above are an example; use the ones your server
documents.

A model that clones on this machine without a server is an adapter, chosen per
voice by its `engine`. See [ADAPTERS.md](./ADAPTERS.md#speech).

## Audition a voice

A cloned voice can say every word right and still be wrong: it may begin a
line with the last words of its own clip. Before a voice is used, hear it say
a handful of lines and have them checked:

```sh
me audition ada                       # eight lines, short and long, into audition/ada-neutral/
me audition ada --lines lines.txt     # your own, one to a line
```

Each line is said once, with no second chances, listened back to and checked
for its words and for the clip leaking in. It passes when nothing leaked and
at least four lines in five were said right, and exits 1 otherwise. A voice
that leaks wants a clip that ends on a plainer sentence, or a longer silence
cut from its end.

Whisper listened; no person did. Listen to the folder yourself before you
call a voice good.
