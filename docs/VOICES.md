# Voices

A voice belongs to a persona and has an emotion. It is a row in the database
that says which adapter speaks it and what that adapter knows it by. There are
two kinds:

- **A voice the speech server has of its own.** The row holds the server's
  name for it, and that is all. A [quick voice](#a-quick-voice) is one of
  these: the row holds the name of one of a small model's own voices, or where
  a table mixed from them is.
- **A voice cloned from a clip.** The row also holds where a short recording
  of the voice is, and the words said in it. The clip is sent to a server that
  clones, or handed to an adapter that does. A voice
  [blended from others](#a-voice-between-two-voices) is one of these: what is
  kept of it is a clip.

No voice and no recording is kept in this repository. Voices that are meant to
be shared are kept in a [library](./LIBRARIES.md), a repository of their own.

**Clone only a voice you have the right to: your own, or one whose owner said
yes to this use.** A library records that for every voice, and one that does
not is refused.

## Say something

```sh
media say --voice ada "The lantern is lit at dusk." out/line.wav
media say --voice ada "Quietly, now." out/hush.wav --emotion whisper --seed 31
```

The words are said, listened back to, and said again with another seed until a
take passes, as every line of a script is. It prints the seed that passed, so
the same take can be had again.

## Make a clip to clone from

What clones best, measured over many voices and models:

| | |
| --- | --- |
| Length | 15 to 28 seconds. Under 12 there is too little of the voice; over 30 most models stop listening |
| Who | one person, in one mood, with nobody else and no music ([a voice with music under it](#a-voice-with-music-under-it) is taken out first) |
| Ending | the quiet after a plain, complete sentence. A model goes on from where its clip stops: one that ends mid-word, or whose words say more than its sound, is heard at the start of every line |
| Pauses | none over three seconds. A model copies a long pause as readily as a word |
| Form | one channel, 24 kHz, at -19 LUFS with peaks under -2 dB |
| Words | exactly what is said in the finished clip, not in the recording it was cut from |

`media reference` does the last two, and tells you about the rest:

```sh
media reference recording.wav                            # measure it, and say what is wrong with it and where to cut it
media reference recording.wav voices/ada/neutral.flac --listen          # cut it where that is likeliest
media reference recording.wav voices/ada/neutral.flac --from 12.5 --to 34 --listen
media reference recording.wav voices/ada/neutral.flac --from 12.5 --to 34 --tighten --listen
```

Measuring says how long the recording is, how loud, whether it stops in the
middle of a sound, and how long its longest pause is. Of a recording longer
than a clip can be, it also says where it could be cut, the likeliest first:

```
where it could be cut, the likeliest first:
  --from 12.9 --to 37.8    24.9 seconds, 24 of them talk, its longest pause 0.5, ending in 1.0 of quiet
  --from 41.2 --to 63.0    21.8 seconds, 20 of them talk, its longest pause 0.7, ending in 0.6 of quiet
```

Each is 15 to 28 seconds that begin where a phrase begins and end in a quiet
long enough to be the end of a sentence, with the most talk and the shortest
pauses it could find. They are found by loudness alone, so listen before you
trust one: that it is one person, in one mood, and that the quiet it ends in
does follow a whole sentence. Cutting with no `--from` and `--to` takes the
first of them and says which it took. With `--listen`, words that were heard
not to end a sentence are remarked on, when the listener writes full stops.

`--to` is where to end
it: in the quiet after a sentence, not at the last moment before the next.
`--tighten` takes the middle out of every pause longer than 0.8 seconds
(`--tighten 0.5` for another length, never under a quarter of a second, where
words run together), and leaves the quiet the clip ends in alone.

The second cuts the part asked for, takes out the rumble under 70 Hz and the
silence at either end, brings it to the loudness and peak above, and writes
the clip. With `--listen` the finished clip is transcribed and its words are
written beside it as `voices/ada/neutral.txt`; read them against the clip and
put right what was misheard. `--said "…"` gives the words instead.

To put the clip straight into a library, with whose voice it is and by what
right, `media library clip` does all of this in one step: see
[LIBRARIES.md](LIBRARIES.md#put-a-voice-in).

Keep the recording the clip was cut from somewhere else. A clip is a few
hundred kilobytes; a library of clips stays small enough to clone in a moment.

## A voice with music under it

Music is not noise. A cleaner takes off what is steady, a hiss or a hum, and
music moves as much as the voice does. Taking one from the other is
separation, and it is a model's work:

```sh
media separate interview.wav parts
```

```
interview.wav: -13.6 dB, pulled apart in 1.7 s by demucs htdemucs
  voice   -16.2 dB  13.2 s  parts/voice.wav
  rest    -17.6 dB  13.2 s  parts/rest.wav
```

`parts/voice.wav` is whoever speaks or sings, and `parts/rest.wav` everything
that was under them. The numbers are how loud each is, so a part with nothing
in it shows: where no voice was found, or nothing under one, the command says
so in words.

Nothing in the engine does this by itself. It needs a
[plugin](./ADAPTERS.md#separation), and the one there is runs a model whose
weights its authors provide "only for scientific purposes". Read what that
plugin's README says before a voice made this way goes into anything:

```sh
pip install ./media-engine-plugins/plugins/demucs-separation
export CONSTRUCTIVE_MEDIA_ADAPTER_SEPARATION=demucs
```

What comes out is not what went in. In one trial, with music about as loud
as the voice, the music came out 13 dB further under it: quiet, and still
there in the pauses. The voice loses a little of itself as well, and a clone
copies both. So listen to `voice.wav` before a clip is cut from it, cut the
clip where the music was thinnest, and prefer a recording of the voice alone
wherever there is one. `media clean` and `media reference` take the file from
there like any other recording.

## Clean a recording first

A clone copies everything in its clip, the hiss and the hum as much as the
person. So a noisy recording is cleaned before a clip is cut from it, and
cleaned as little as will do, because every cleaner takes some of the voice
with the noise and a clone copies that loss as well.

```sh
media clean recording.wav              # measure it, and say what is worth doing
media clean recording.wav cleaned.wav  # do that, and say what it gained and what the voice lost
media clean recording.wav cleaned.wav --steps highpass,notch --hum 50
```

What is measured is the quiet between phrases, the only place noise is heard
alone:

| It finds | It does |
| --- | --- |
| more than 45 dB between the voice and the quiet | nothing: the recording is left alone and nothing is written |
| rumble under 80 Hz | a high-pass filter, which no voice minds |
| a hum at 50 or 60 Hz | notches at it and at what rings above it |
| hiss, or noise where the voice sits | the [denoiser](./ADAPTERS.md#denoise), and then a listen: it takes a little of the voice too |
| no quiet at all | nothing. Music or a crowd under a voice is a second sound, not noise, and no denoiser takes it off: [`media separate`](#a-voice-with-music-under-it) does |

After a cleaning the same stretches are measured again. It says how many
decibels further under the voice the noise now is, and how much the voice
lost; a voice that lost more than a decibel was damaged, and it says that too.

## Give it to a persona

```sh
media voice ada the-servers-name-for-a-voice --new                    # a voice the server has
media voice ada ada --reference voices/ada/neutral.flac --new         # a voice cloned from a clip
media voice ada ada-hushed --reference voices/ada/whisper.flac --emotion whisper
media voices
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
media audition ada                    # eight lines, short and long, into audition/ada-neutral/
media audition ada --lines lines.txt  # your own, one to a line
```

Each line is said once, with no second chances, listened back to and checked
for its words and for the clip leaking in. It passes when nothing leaked and
at least four lines in five were said right, and exits 1 otherwise. A voice
that leaks wants a clip that ends on a plainer sentence, or a longer silence
cut from its end.

Whisper listened; no person did. Listen to the folder yourself before you
call a voice good.

## A voice between two voices

A model that clones hears a clip as a row of numbers before it says anything.
A row part of the way from one voice's to another's is a voice that neither of
them has, and `media blend` says something in it:

```sh
media blend ada:0.35 grace:0.65 out/between.wav                         # a passage of about twenty seconds
media blend ada:0.35 grace:0.65 out/line.wav --words "The lantern is lit."
media blend ada grace lin:0.5 out/three.wav --seed 31                   # more than two, and another reading
```

Each voice is a persona whose voice was cloned from a clip, or a recording,
and after the colon how much of it is wanted. Shares are in proportion:
`ada:1 grace:2` is a third and two thirds, and a voice with no number has a
whole share. What was said is listened back to and said again until a take
passes, as every line is, and then it is heard by the speaker model, which
says how like each of its voices it came out:

```
seed 4100, 1 take, clean: out/between.wav
  ada    35%   0.16 alike
  grace  65%   0.42 alike
```

Those numbers follow the shares and do not equal them. Two clips of one person
come out near 0.7 or over and two people under 0.4, and a blend is a third
person: the nearer the middle it is, the less it is like either. One that
scores near 0.7 against a voice is that voice, with too little of the other
to hear, and should be given less of it. Another `--seed` is another reading
of the same voice. Then listen. Nothing here can tell you whether it is a
voice you want.

It takes a speech adapter that hears a voice as numbers and can say a line
from numbers. The [`mlx` adapter](./ADAPTERS.md#speech) does, with the model
it runs by default: `CONSTRUCTIVE_MEDIA_ADAPTER_SPEECH=mlx`, or `--engine mlx`
for this command alone. The voices are blended as that one model hears them.

### Keep it

```sh
media blend ada:0.35 grace:0.65 voices/wren/neutral.wav --keep wren
media audition wren
```

With `--keep`, the passage is cut as a clip to clone from, at the rate and
loudness every clip is kept at, with its words in the `.txt` beside it, and
the persona is given it as a voice, and added if they are new. From then on it
is a cloned voice like any other: `media say --voice wren`, a script, a
library, and any adapter or server that clones can speak it, with nothing
blended again. What it was made of is written beside the clip, in a file
ending `.blend.json`: the voices, their shares, the model and the seed.

Nothing is kept of a blend no take of which passed. A kept voice has still
only said one passage: audition it before it is used.

**A blend of two people's voices is made of two people's voices.** It takes
the same permission from each of them as cloning either would, and saying so
is yours to do when it goes into a library.

## A quick voice

A model that clones takes a few seconds over a sentence. That is nothing in a
script and a long time in a conversation. [Kokoro](./MODELS.md#speech-text-to-a-voice)
is a small model that says a sentence in about a tenth of a second on a Mac,
and the [`kokoro` adapter](./ADAPTERS.md#a-small-quick-model) runs it. It does
not clone. It comes with a few dozen voices, and it speaks in one of those:

```sh
media talk --profile af_heart          # with CONSTRUCTIVE_MEDIA_ADAPTER_SPEECH=kokoro
media voice ada bm_george --engine kokoro
```

The first letter of a voice's name is what it speaks, `a` American English and
`b` British, and the second whether it is a woman's or a man's.

### One that leans towards a recording

Each of the model's voices is a table of numbers, and a table that is part of
one and part of another is a voice too. `media mimic` looks for the mix that
sounds most like somebody:

```sh
media mimic ada.flac voices/ada.safetensors                  # a recording
media mimic ada voices/ada.safetensors                       # or a persona whose voice was cloned from a clip
media mimic ada.flac voices/little-ada.safetensors --keep "Little Ada"
media talk --voice little-ada
```

It says three sentences in each of the model's English voices and asks the
speaker model how like the recording each one is. Then it starts from the
nearest and tries mixes of the nearest six, 120 of them unless `--rounds` says
otherwise, keeping a mix whenever it came out nearer. That takes about a
minute. It writes the table, and beside it a passage said in the new voice and
a file ending `.mimic.json` that says what the voice is a mix of:

```
voices/ada.safetensors: 0.41 alike ada.flac; the nearest of the model's own voices alone, bf_alice, is 0.41
  af_bella      26%
  af_heart      23%
  bf_isabella   21%
  bf_alice      16%
  bf_emma       10%
  bf_lily        3%
said in it: voices/ada.wav (seed 4100, 1 take, clean)
```

**It is not a clone, and the numbers say so.** A voice cloned from a recording
comes out about 0.8 alike it. On three test recordings a mimicked voice came
out 0.18, 0.36 and 0.41: the same sort of voice, at about the same pitch, and
nobody's friend would take it for them. The second number on the first line is
the same passage in the one voice of the model's own that was nearest to begin
with. Over the sentences a mix was found with, it was 0.01 to 0.05 nearer than
that voice. Over a passage it was not found with it was within 0.02 of that
voice on all three recordings, a little nearer on one and a little further on
two, and the command says so when the mix is no nearer. What it reliably tells
you is which of the model's voices to start from. Listen to both before you
choose the mix.

With `--keep` the persona is given the table as a voice that the `kokoro`
adapter speaks, whatever adapter speaks everybody else, so one machine can
say a script in cloned voices and talk in a quick one. A persona has one voice
for each emotion: keep a mimicked voice under a name of its own, or it takes
the place of the cloned one. The same `--seed` finds the same mix.

**A voice made to lean towards somebody is made from their recording.** It
takes the same permission as cloning them would.

## A recording said again in another voice

A model that clones is given words and says them its own way. A model that
converts is given a recording: it keeps the words, their timing and their
tune, and changes whose voice it is. That is the one to use when how a line
was said matters more than who said it, as a reading you did yourself for a
character who is not you.

```sh
media revoice take.wav out/take-as-ada.wav --voice ada      # a persona whose voice was cloned from a clip
media revoice take.wav out/take-as-ada.wav --like ada.flac  # or any recording of the voice
```

Nothing in the engine does this by itself. It needs a
[plugin](./ADAPTERS.md#voice-conversion), and the one there is carries the
GPL:

```sh
pip install ./media-engine-plugins/plugins/seedvc-conversion
export CONSTRUCTIVE_MEDIA_ADAPTER_VOICE_CONVERSION=seedvc
```

It prints how like the voice the recording was to begin with and how like it
what was written is, as the speaker model hears them:

```
out/take-as-ada.wav: 0.79 alike ada, where take.wav was 0.27
  15.5 s said again in 5.4 s by seedvc Plachta/Seed-VC
```

A clone comes out about 0.8 alike its clip, so that one is as near as a
clone. They are not all. With the same recording and another voice's clip the
second number was between 0.38 and 0.53: plainly moved towards the voice, and
not it. The number is there so you know which you have. Where it is no higher
than the first, the command says the conversion did nothing you could use.

Most of those 5.4 seconds were the model being loaded. `media revoice` does
that for every recording, and a program that keeps the adapter
(`constructive_media.conversion.converter()`) does it once: after that the
same quarter of a minute took a second and a half.

### In a conversation

A cloned voice takes seconds over a sentence and a [quick voice](#a-quick-voice)
is nobody's in particular. The two can be put end to end: the quick voice says
the sentence and the converter says it again in the persona's.

```sh
media talk --voice ada --quick af_heart
```

`--quick` names the voice the words are first said in, one that the
configured `speech` adapter has of its own. `--voice` has to be a persona
whose voice was made from a clip. On one desktop the first sentence of an
answer took 0.7 to 1.0 seconds that way, where the quick voice alone took 0.16
([the wait](./TALK.md#the-wait)). The tune of the sentence is the quick
voice's, since that is what the converter was handed.

**The voice a recording is said again in is somebody's, and so is the
recording.** It takes the same permission as cloning the voice would, and the
say-so of whoever's reading it is.
