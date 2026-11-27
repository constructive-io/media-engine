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
| Ending | the quiet after a plain, complete sentence. A model goes on from where its clip stops: one that ends mid-word, or whose words say more than its sound, is heard at the start of every line |
| Pauses | none over three seconds. A model copies a long pause as readily as a word |
| Form | one channel, 24 kHz, at -19 LUFS with peaks under -2 dB |
| Words | exactly what is said in the finished clip, not in the recording it was cut from |

`me reference` does the last two, and tells you about the rest:

```sh
me reference recording.wav                               # measure it, and say what is wrong with it
me reference recording.wav voices/ada/neutral.flac --from 12.5 --to 34 --listen
me reference recording.wav voices/ada/neutral.flac --from 12.5 --to 34 --tighten --listen
```

Measuring says how long the recording is, how loud, whether it stops in the
middle of a sound, and how long its longest pause is. `--to` is where to end
it: in the quiet after a sentence, not at the last moment before the next.
`--tighten` takes the middle out of every pause longer than 0.8 seconds
(`--tighten 0.5` for another length, never under a quarter of a second, where
words run together), and leaves the quiet the clip ends in alone.

The second cuts the part asked for, takes out the rumble under 70 Hz and the
silence at either end, brings it to the loudness and peak above, and writes
the clip. With `--listen` the finished clip is transcribed and its words are
written beside it as `voices/ada/neutral.txt`; read them against the clip and
put right what was misheard. `--said "…"` gives the words instead.

Keep the recording the clip was cut from somewhere else. A clip is a few
hundred kilobytes; a library of clips stays small enough to clone in a moment.

## Clean a recording first

A clone copies everything in its clip, the hiss and the hum as much as the
person. So a noisy recording is cleaned before a clip is cut from it, and
cleaned as little as will do, because every cleaner takes some of the voice
with the noise and a clone copies that loss as well.

```sh
me clean recording.wav                 # measure it, and say what is worth doing
me clean recording.wav cleaned.wav     # do that, and say what it gained and what the voice lost
me clean recording.wav cleaned.wav --steps highpass,notch --hum 50
```

What is measured is the quiet between phrases, the only place noise is heard
alone:

| It finds | It does |
| --- | --- |
| more than 45 dB between the voice and the quiet | nothing: the recording is left alone and nothing is written |
| rumble under 80 Hz | a high-pass filter, which no voice minds |
| a hum at 50 or 60 Hz | notches at it and at what rings above it |
| hiss, or noise where the voice sits | the [denoiser](./ADAPTERS.md#denoise), and then a listen: it takes a little of the voice too |
| no quiet at all | nothing. Music or a crowd under a voice is a second sound, not noise, and no denoiser takes it off |

After a cleaning the same stretches are measured again. It says how many
decibels further under the voice the noise now is, and how much the voice
lost; a voice that lost more than a decibel was damaged, and it says that too.

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
