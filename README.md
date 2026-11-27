# media-engine

<p align="center" width="100%">
  <img height="250" src="https://raw.githubusercontent.com/constructive-io/constructive/refs/heads/main/assets/outline-logo.svg" alt="Constructive" />
</p>

<p align="center" width="100%">
  <a href="https://github.com/constructive-io/media-engine/actions/workflows/ci.yml">
    <img height="20" src="https://github.com/constructive-io/media-engine/actions/workflows/ci.yml/badge.svg" alt="CI status" />
  </a>
  <a href="https://github.com/constructive-io/media-engine/blob/main/LICENSE"><img height="20" src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT license" /></a>
</p>

<p align="center" width="100%">
  Constructive Media: one engine for video, images, audio and voice, with Postgres as its memory.
</p>

```bash
npx skills add constructive-io/media-engine
```

Constructive Media treats everything it ingests or generates as data. Footage,
stills, recordings and renders are rows; so are the people in them, the shots
and words found inside them, the cuts made from them and every judgement a
person or a model passed along the way. Models come and go behind adapters. The
record of what was made, from what, and why stays in one database.

## Status

Early, and built in the open one layer at a time.

| Layer | State |
| --- | --- |
| Schema modules (`packages/`) | Here, tested, audited |
| Settings and the adapter interface for the database, models and effects (`python/`) | Here, tested |
| Engine and `media` command line: ingest, lineage, shots, frames, signatures, detection, transcription, takes and sync, speakers, the sound measured (how loud a shot is against what surrounds it, how well a line can be heard, where people laugh) and the movement of what is lit against the rest of the picture, passages, flags, scores and rankings, decisions, shot selection and timelines, a ranker taught from preferences, interviews assembled from passages with footage laid over them, cuts rendered to a file and exported to an editing program, a text model's opinion of the best passages, search by meaning | Here, tested |
| [`.conv`](https://github.com/constructive-io/conversation-format) scripts read and checked, held to the format's own conformance files | Here, tested |
| The mix: clips on named tracks, brought to a level, looped, faded, ducked under speech and put through effects, written with a stem for each track | Here, tested |
| The check on a spoken line: what was heard against what was meant, a voice's reference recording leaking into the line, a stutter, a line cut off, a click at the start | Here, tested |
| Speech: voices kept by persona and emotion, a client for any server that speaks, and a line said again with another seed until a take of it passes the check | Here, tested with stand-ins for the models |
| `.conv` scripts rendered to sound: every line said and checked, every sound put in its place, the whole mixed to one loudness, with the lines, the stems and a manifest kept beside it | Here, tested with stand-ins for the models |
| [The stage](./docs/STAGE.md): a rendered script filmed as a video. A page follows the recording, as a deck, a document or a walkthrough, with the speakers, captions and clips drawn over it; a browser with no window is watched, and the pictures are laid under the sound | Here, tested, and filmed with a real browser |
| [Voices](./docs/VOICES.md): a noisy recording measured and cleaned, a recording cut into a clip a voice can be cloned from, the clip sent to a server that clones or to a model run on a Mac with no server, and a voice auditioned before it is used | Here, tested with stand-ins for the models |
| [Libraries](./docs/LIBRARIES.md): voices, sound banks and faces in repositories of their own, each voice with its consent, added by address and kept at a commit | Here, tested |
| [Starting a project](./docs/PROJECTS.md): `media init` makes a workspace and its decks from a repository of boilerplates, in the format `pgpm init` reads | Here, tested |
| Pictures and video made from a description by a model on your own machine, every request and what it made kept on record apart from what was filmed | Here, tested with stand-ins for the models |
| [Talking with a model](./docs/TALK.md): a page on your own machine that hears you, answers with a text model and says the answer back a sentence at a time, and stops when it is spoken over | Here, tested with stand-ins for the models |
| Engine: sounds made from a description, a voice re-spoken in another, a face re-timed to new speech | Being brought in |

The roadmap is in [docs/ROADMAP.md](./docs/ROADMAP.md).

## What is in the box

| Module | Schema | Holds |
| --- | --- | --- |
| [`media-core`](./packages/media-core) | `media` | Personas, voices, assets, takes, episodes and what was derived from what |
| [`media-video`](./packages/media-video) | `media_video` | Shots, frames, transcripts, passages, timelines, scores and editing decisions |
| [`media-audio`](./packages/media-audio) | `media_audio` | Emotions, text models, renders and human review |

`media-core` is the spine: a persona is the same row whether a model is
speaking in their voice or recognising it in a recording. The other modules
depend on it and never on each other.

The Python engine in [`python/constructive_media`](./python/constructive_media)
needs only a database driver; each part of it adds what it uses as an optional
extra. It reaches the database, every model and every effect through an
[adapter](./docs/ADAPTERS.md), so a model is swapped with a setting and a tool
under another license plugs in from outside this repository. Video and audio
are read and written by the `ffmpeg` program.

The engine ships no models, voices, footage or people. You bring your own.

## Installation

```sh
git clone https://github.com/constructive-io/media-engine
cd media-engine
pnpm install
```

You also need PostgreSQL 17+ with [pgvector](https://github.com/pgvector/pgvector)
and [pgpm](https://www.npmjs.com/package/pgpm) (`npm install -g pgpm`). See
[Prerequisites](#prerequisites).

## Usage

Deploy the schema into a database of your own:

```sh
# Docker, or skip these two lines and export PGHOST/PGPORT/PGUSER/PGPASSWORD
pgpm docker start
eval "$(pgpm env)"

# media-video and media-audio bring media-core with them
pgpm deploy --database media --createdb --yes --package media-video
pgpm deploy --database media --yes --package media-audio
```

Install the engine with Python 3.11 or later. You also need `ffmpeg` on your
path, and [whisper.cpp](https://github.com/ggml-org/whisper.cpp) for
transcription (`brew install whisper.cpp`):

```sh
pip install ".[video,detection,speakers,audio]"   # or just `pip install .` for the core
export CONSTRUCTIVE_MEDIA_DATABASE_URL=postgres://postgres:password@localhost:5432/media
```

Then point it at some media:

```sh
media ingest ~/footage --project demo  # register every file, once
media shots                          # split each video into shots
media frames                         # save and measure sample frames
media signatures                     # a colour and motion signature per shot
media detect                         # find objects and people in the frames
media vocabulary Zentrova "Ottoline Varga" --project demo  # names to listen for
media transcribe                     # what is said, sentence by sentence, word by word
media vocabulary Zentrova --heard-as "zen trova" --project demo  # what a name is misheard as
media mend                           # put right every mishearing on record, and keep what was heard
media relisten                       # transcribe the weakest recordings again, and keep what is better
media group                          # recordings that rolled together become a take
media match                          # a recorder with no clock finds its camera by what was said
media sync                           # place every recording on its take's timeline, and prove it
media takes                          # each take and how much of it is placed
media overlap interview desk         # the sentences two recordings share, and the offset each one implies
media pull interview --at 1:20       # the same moment cut from every recording, to hear whether they are in step
media take interview --offset desk -31.25  # say an offset outright; sync builds on it and never moves it
media diarize                        # tell the voices in each recording apart
media speakers                       # each recording's voices, and a clip of each to listen to
media name interview voice1 "Ottoline Varga" --new  # say whose voice it is
media wear interview collar "Ottoline Varga"  # say who wore a microphone
media microphones interview          # give each line to whoever's microphone heard it loudest
media loudness                       # how loud each shot is against the minute around it
media coordination                   # whether the lit part of each shot moves with the rest of it
media audible                        # how well each line can be heard: the noise under it, and distortion
media laughter                       # where people laugh, as candidates to say yes or no to
media passages                       # cut each take's speech into passages worth using
media score                          # flag what to cut, then score every passage and shot
media rank interview --top 10        # the best of them, and how much each was measured
media judge                          # a text model reads the best of them and says whether anything is said
media index                          # make every passage findable by what it means
media search "the winter the boats came in close"      # the passages that answer to it
media search "why the light went out" --cut dark --seconds 60  # or a cut of them
media script --out script.md         # the best passages as a document to mark keep, cut or trim
media script --read script.md        # store the marks as decisions
media compile reel --count 12        # shots that are good and unalike, stored as a cut
media note reel 3 drop               # say what you made of a clip
media compile reel-2 --after reel    # the next cut: what was kept stays, what was dropped is replaced
media pairs --render pairs/          # the shots a ranking cannot tell apart, side by side to choose between
media prefer 3f9a01c2 77b0de41       # which of two you would use
media fit --save taste               # a model of what you prefer, tested on footage held out from it
media recipe reel --like broll --weigh preferred=0.5  # a recipe that ranks by it
media assemble talk --take interview  # the best passages as a cut, each seen and heard through the right recording
media cutaways talk-over --over talk  # other footage over some of it, the sound untouched
media flags talk-over                # every flag inside the cut, and what cutting them would save
media render talk-over               # the cut as a file to watch
media relink talk-over talk-table table  # the same cut heard through another recording of its take
media render talk-over --mix         # or through its worn microphones, mixed to follow whoever is speaking
media export talk-over --to handover/ --preview --renders  # the cut for an editing program, with something to watch beside it
media lineage out.mp4                # what a derived file was made from
media outputs                        # every derived file on record, and what is lying about that is not
media generate image "a harbour at dawn, fog over the water"  # a picture from a description, on record as generated
media generate video "fog lifting off the harbour" --seconds 2  # or a piece of video
media generations                    # every request, how it came out and where the file is
media synthetic talk-over            # the clips of a cut that nobody filmed
media persona "Ada Field" --new --kind character --bio "Keeps the light."  # who somebody is
media personas                       # everybody, and the voices each has
media conv scene.conv                # check a .conv script: every problem, its line and how to mend it
media voice "Ada Field" ada-plain --new # give a persona a voice, by the name the speech server knows it by
media say scene.conv out/scene.flac --stems  # the script as sound, each line said until a take of it passes
media status                         # where things are kept and what is held
media adapters                       # every slot and what can fill it
```

Every stage picks up where it left off and only works on what is new. A model
is downloaded the first time a stage needs it: 1.6 GB for the default
transcriber, 29 MB for the default speaker model.

A transcript is kept right without listening to everything again. A term in
the vocabulary can carry what the recogniser writes in its place, and
`media mend` rewrites exactly those, in the lines and in their words, keeping
a plural or a possessive. What the recogniser wrote stays beside each line
and word and every change is recorded, so `media mend --dry-run` shows what
would change and `media mend --undo` puts all of it back. `media relisten`
finds the recordings the recogniser was least sure of, or wrote little for,
and listens to them again with another adapter or a larger model; the new
transcript replaces the old only where it is surer and found no less.
`media transcribe --from FILE` listens to another file in place of one
recording, the same sound cleaned up, and `--speaker NAME` writes a name
beside every line of a microphone only one person wore.

A take is the camera and the microphones that recorded one moment. `media sync`
works out how far apart they started from the words they share, then from how
the sound rises and falls, then from the devices' clocks once those are shown
to agree. Every answer is checked against what is actually said at the same
moment in both, and one that fails the check is not stored. `media sync --check`
runs that check on what is stored and changes nothing.

Sometimes there is nothing to go on: a recorder whose clock was never set, a
camera that heard only music. A person who was in the room still knows which
files belong together, so `media take` makes a take by hand or mends one:
which recordings are in it, what each is to it, which is the primary, and how
far each is from the primary. An offset said there is stored as `manual`, and
`media sync` builds on it and never moves it. Two commands give the evidence
to say it on. `media overlap` shows the sentences a recording shares with
the take's primary, the time each says them and the offset each sentence
implies, beside the offset the take has stored; it reads transcripts and
changes nothing. `media pull` cuts the same stretch of take time out of every
recording: if the offsets are right, every file says the same words from the
same instant, and each file is recorded with the offset it was cut under.

`media diarize` listens to what was transcribed and finds how many voices there
are and which lines each one spoke. It does not know who anyone is. It calls
them `voice1`, `voice2` and so on and cuts a short clip of each; you listen and
say who it is with `media name`, once, and every line that voice spoke carries the
name. A voice already named in one recording is suggested when it is heard in
another, and never applied without you. An hour and three quarters of talk is
gone over in about fifteen seconds.

Where everyone at a table wears a microphone there is a second way, and it
needs no model. `media wear` says who wore which recording of a take, and
`media microphones` gives each line to the wearer of the microphone that heard
it loudest, each measured against its own loud level so that a recorder turned
up high does not win everything. A line no microphone leads clearly is left
without a name, and a line a person named is never changed. The two ways
check each other: each microphone's lines are counted by the voices
`media diarize` found, and one that carries more than one voice is said to.

`media passages` reads a take as one conversation, however many microphones
heard it, and cuts it where the talking stops. `media score` flags the fillers,
stutters, dead air and lines the recogniser guessed at, then scores each
passage and shot from what was read, heard and seen. A score nobody measured
is left out, never counted as a zero, and `media rank` says how much of its
recipe each result was measured on. A passage in which someone asks for
something not to be used is never ranked, whatever it scores, until a person
dismisses that flag. `media flag` runs the flags on their own.

Four commands measure what the words and the pixels do not say, and
`media score` turns each into scores. `media loudness` gives a shot its lift:
how far it sits above the minute around it, which is where a cheer or a gasp
shows (`energy_lift`). `media coordination` follows what is bright and
strongly coloured in a shot, and everything else, and says how closely the
two move together over the shot's best four seconds (`coordination`).
`media audible` measures each transcribed line for itself: the noise under
it, how far the speech stands above that, and how much of it was cut off at
the top of the scale (`audio_snr`, `undistorted`, `spectral_balance`). A
recogniser is as sure of a muffled line as of a clear one, so this is the
only measure of whether anyone would want to listen, and `media assemble`
takes each passage's sound from the cleanest recording measured.
`media laughter` finds where people laugh, from the sound, since a
recogniser throws laughter away. What it finds are candidates:
`media laughter --yes ID` or `--no ID` says which are right, and a passage
is credited with a laugh in it or in the six seconds after (`laughter`).

The recipes that come with the engine weigh `energy_lift` and
`coordination` for shots. The others count once a recipe weighs them:
`media recipe talk --like interview --weigh audio_snr=0.5 --weigh laughter=0.5`.

`media judge` asks what no measurement can: whether anything is said. A passage
of thanks scores as well as an explanation on every measure of form, so the
best-ranked passages, most said in the least time first, are read by a text
model, and what it makes of each is stored as three more scores with the
model's name on them: `substance`, `interest` and `self_contained`. They are
scores like any other, counted by a recipe that weighs them
(`media recipe said --like interview --weigh substance=1`), and a passage is
read once by each model. `media index` keeps every passage as an embedding and
`media search` finds the ones nearest a question in meaning, weighed with how
good each is, so a passage that is on the subject and unusable does not come
first. `--answers` has the text model check that each one answers the
question and is not only about it, and `--cut` stores what was found as a
cut. A passage whose words have been corrected drops out of search until it
is indexed again.

Both models are reached over HTTP, on any server that speaks the
chat-completions and embeddings API: llama.cpp, Ollama, vLLM, or a hosted
one. Nothing is sent until you name the server and the model, so your
transcripts go where you send them and nowhere else, and a passage in which
someone asks for it not to be used is never sent at all:

```sh
export CONSTRUCTIVE_MEDIA_URL_TEXT=http://localhost:11434/v1
export CONSTRUCTIVE_MEDIA_MODEL_TEXT=the-name-the-server-knows-it-by
export CONSTRUCTIVE_MEDIA_URL_EMBEDDING=http://localhost:11434/v1
export CONSTRUCTIVE_MEDIA_MODEL_EMBEDDING=a-model-that-gives-1024-numbers
```

`CONSTRUCTIVE_MEDIA_KEY_TEXT` and `CONSTRUCTIVE_MEDIA_KEY_EMBEDDING` hold a key
for a server that wants one. `CONSTRUCTIVE_MEDIA_EXTRA_TEXT` is a JSON object
sent with every request, for whatever a server calls its own options. A model
that thinks before it answers needs it, either to be told not to
(`{"reasoning_effort": "none"}`, on a server that takes that) or to be allowed
enough for both (`{"max_tokens": 2000}`); `media judge` says so when a model
spends everything it was allowed and answers nothing.

Try a model on a few passages you know before trusting it. One that marks
filler as highly as an explanation adds noise, and small ones are strict to a
fault about what answers a question.

What a person decides is kept, with who decided and when. `media keep` and
`media cut` rule a passage or a shot in or out, everywhere or for one episode,
and `media prefer` records which of two was better. `media script` writes the
best passages out as a plain document in the order they were said; mark
each one and `media script --read` stores the marks, and saying the same thing
twice stores it once.

`media compile` chooses shots for a cut. The best shots by score are the same
shot over and over, so each pick is weighed for its quality less how much
it looks like what is already picked, and the picks are ordered so that
neighbours differ. Every run says how alike its neighbours are beside what
the top of the list would have given. A `--seed` makes a variant, and the
same seed makes the same one. A cut is revised by reacting to its clips with
`media note` and compiling the next one `--after` it. `media timelines` lists the
cuts and `media timeline` shows one.

`media fit` teaches a model from the choices made with `media prefer`: which
measurements of a shot go with being chosen, and by how much. It is tested on
footage it was not fitted to, a whole shoot held out at a time, since shots
from one shoot are alike enough to flatter a model that has seen their
neighbours. The share it gets right is shown beside the half a coin would
get. A model that has not beaten the coin by more than chance is stored with
its figures and left unused. One that has gives every shot a `preferred`
score, and a recipe made with `media recipe` can weigh it beside the rest.

`media assemble` makes an interview into a cut. The passages a person kept go
in first, then the best by a recipe until the cut is as long as it was asked
to be, and every passage passed over is counted with the reason. Each one is
seen through the take's main camera, or another that was rolling, and heard
through a microphone before a camera: the one measured cleanest, or failing
that the one whose words the recogniser was surest of. Quiet at either end
of a passage is trimmed to what the style allows; only the ends move, so a
filler in the middle of a passage stays where it was said. `media styles` lists
the stored manners of cutting, and any one setting can be given on its own.
A cut made `--after` another keeps the clips that were kept or adjusted,
with their sound still in step, and leaves out what was dropped.

`media cutaways` lays other footage over some of a cut's clips while the sound
carries on underneath: the middle of a long clip, or the whole of a short
one, never the first. It leaves out shots with somebody filling the frame
and anything recorded in the takes the cut is made from. A cutaway that was
dropped is replaced by another of the same length.

`media render` makes a cut into a file. Every clip is given a whole number of
frames counted from the start of the cut, so rounding never adds up, and the
sound is cut to the same count in samples: a long cut ends as much in step as
it began. Sound that carries on from one clip to the next in the same
recording is read as one unbroken stretch, with no join to hear under a
cutaway; where it does jump, it fades out and in over a fiftieth of a second.
Each stretch is measured and brought to one loudness, with one gain for the
whole of it and a limiter under a ceiling, or left `--as-recorded`. The
picture is padded to the canvas or cropped to fill it, and a crop moves to
where the people in the shot were found. It reads the small copies made at
ingest unless `--originals` is given, and `--megabytes` holds the file to a
size. When it is done it looks at frames through the file and says so if
they are not all the size that was asked for, and the file is recorded with
the recordings it was made from, so `media lineage` can answer for it.

A cut is made from the recording that was easiest to read, which is seldom
the one that sounds best. `media relink` stores it again with each clip's
sound moved to the same moment in another recording of its take, by the
offsets `media sync` found: a recorder on the table in place of the camera's
own microphone. A clip the recording does not cover is left as it was and
listed. `media render --mix` goes further where people wore microphones.
One microphone is right while one person talks, and when the speaker changes
everyone else is heard through somebody else's. So under each stretch the
take's worn microphones are mixed the way an editor rides faders: whoever is
speaking is up, a little before their first word, and the others are down by
`--duck` decibels and never off, because they carry the room and the
laughter. Through a pause the last speaker stays up, and a recorder that was
turned down is brought level with the rest. A stretch the microphones do not
cover is left as cut, and the render says which were mixed and who led.

`media export` hands a cut to an editing program. It writes FCPXML, which Final
Cut, Resolve and Premiere import as a timeline, the same cut as a CMX3600
edit decision list for anything else, and a page that says what both point
at. Nothing is copied: the files name the original recordings where they are
kept. Times are exact fractions of a second at the rate the footage was shot
at, so 29.97 is 30000 frames in 1001 seconds and not a rounding of it, and
each is given on the recording's own clock where it has one: the timecode a
camera wrote for its first frame, drop-frame or not, and the time after
midnight a sound recorder stored. A clip heard through another recording has
that sound under it and the camera's own switched off. A cut whose footage
was shot at more than one rate says so, and so does one that points at a
recording that is not there.

A folder to send somebody is the same command: `--preview` adds the cut
rendered small, held under `--megabytes`, and `--renders` copies in every
render already made of it, and the page lists them. `--title` names the cut
for the editing program. Before it goes, `media flags` lists every flag that
falls inside the cut's clips, on whichever recording of the take it was
heard, and says how many seconds cutting the ones marked to cut would take
out; it fails if the cut holds something someone asked not to be used.

`media outputs` is the other way round from `media lineage`: every derived
file on record with what it was made from, the ones whose file is gone
included, and then the pictures and sound in the work folder that nothing on
record accounts for. Those are what to look at before clearing a folder out.
It deletes nothing.

`media generate` hands a description to a model and keeps what it makes apart
from what was filmed. `image` is filled by [mflux](https://github.com/filipstrand/mflux)
running FLUX.2 klein and `video` by Wan through diffusers, both on your own
machine and both installed by you; [docs/ADAPTERS.md](./docs/ADAPTERS.md#pictures-and-video)
says how, and how long each takes. Every request is a row, with its words,
model, settings and seed, whether it worked or not, and `media generations`
lists them, or adds them up with `--spend`. The same request is made once:
asked again, the command prints the file it has, and `--again` makes another
with a seed of its own. `--from` starts from a picture, `--max-usd` refuses a
request its maker prices above that, and `--dry-run` makes nothing. An adapter
for a service that takes minutes is handed the request and `media collect`
brings in what it made. What is made is a derived file of the kind
`generated`, so `media lineage` answers for it, and `media synthetic` lists
the clips of a cut whose picture or sound a model made. It fails when there
are any: the question to ask before a cut is shown as a record of what
happened.

`media persona` records who somebody is: a real person or an invented
character, and a few words about them. `media personas` lists everybody with
the voices found in recordings that were named as theirs and the voices that
can speak as them.

`media conv` reads scripts in the [`.conv` format](https://github.com/constructive-io/conversation-format):
who speaks, what they say, and the sounds and music under them. It reports
everything wrong in a script at once, each problem with its line, what is
wrong and the nearest thing that would be right, and `--json` prints the
scene the script describes. `@include NAME` finds `NAME.sounds` in a `banks`
folder beside the script, or wherever `--banks` says. The reader passes the
format's own conformance files, which are kept in
[`tests/fixtures/conversation-format`](./tests/fixtures/conversation-format), and it runs no
model.

`media say` renders a script. Everybody who speaks needs a voice (`media voice`),
and a server that speaks and a model that listens need to be set up; see
[docs/ADAPTERS.md](./docs/ADAPTERS.md#speech). To hear a script before you
have a speech model, [`examples/mac-voice-server.py`](./examples/mac-voice-server.py)
answers with a Mac's own voice, and [`examples/scene.conv`](./examples/scene.conv)
is a scene to try. Each line is said, listened
back to and checked against its words, and said again with another seed until
a take passes. Sounds are files the script names, looked for beside the
script or in the folders given with `--sounds`. The mix is turned up or down
whole to -16 LUFS, and squeezed only if its peaks would otherwise pass -1.5 dB.
Beside `out/scene.flac` are kept:

```
out/scene.lines/line_001.wav    every line as it was said
out/scene.stems/dialogue.wav    each track on its own, with --stems
out/scene.manifest.json         each line's seed, takes and verdict, and where everything landed
```

A line no take of which passed is kept in the scene, marked `needs_review` in
the manifest and put in `media_audio.review_queue` for a person to listen to.
An echo or a room put on a line rings on under whatever follows: the next
line comes in when the words end. `media say scene.conv out/scene.flac --only ada,7`
says again just one persona's lines and line 7, and keeps every other line as
it was. A sound described by
a `prompt:` cannot be made yet: give it a `file:`, or pass
`--without-generated` to leave such sounds out.

The same engine from Python:

```python
from constructive_media import conv, effects

effects.apply("line.wav", "line-wet.wav", effects.Effects(reverb="hall", gain=-3))
scene = conv.load("scene.conv")    # or conv.parse(text); ConvError lists every problem

from constructive_media.audio import scene as scenes

report = scenes.render("scene.conv", "out/scene.flac", stems=True)   # the manifest, as a dict
scenes.record(report)                                                # the render, its lines and what to listen to
```

A rendered script is filmed by a page that follows it. A script's
`[slide …]`, `[speakers …]` and `[video …]` marks say what is on the screen;
the page is the built-in one, or any HTML of your own:

```sh
media film out/scene.flac out/scene.mp4                 # the speakers and what they say
media film out/talk.flac  out/talk.mp4 --page examples/talk  # a deck that follows the talk
media still out/talk.flac out/stills --page examples/talk    # pictures of it at moments of the talk, in seconds
media stage out/talk.flac --page examples/talk          # the same in your browser, to scrub through
```

Decks, documents that scroll with the voice, and walkthroughs with a pointer
are in [docs/STAGE.md](./docs/STAGE.md). It needs a Chrome or Chromium that is
already installed.

A voice can be one the speech server has, or one cloned from a short clip of
a voice you have the right to use:

```sh
media clean recording.wav              # is it clean enough to clone from, and what would mend it
media reference recording.wav voices/ada/neutral.flac --from 12.5 --to 34 --listen
media voice ada ada --reference voices/ada/neutral.flac --new
media say --voice ada "The lantern is lit at dusk." out/line.wav
media audition ada
```

On a Mac the speech can come from a model run in the engine's own process, with
no server: `pip install mlx-audio` and `CONSTRUCTIVE_MEDIA_ADAPTER_SPEECH=mlx`
([docs/ADAPTERS.md](./docs/ADAPTERS.md#on-a-mac-with-no-server)).

The models that listen, write and speak can hold a conversation between
them. What you say is written down, answered and said back, a sentence at a
time, and it stops when you speak over it:

```sh
media talk --voice ada     # prints an address on this machine; open it and speak
media talk --measure       # how long each model takes, and the wait they add up to
```

It is in [docs/TALK.md](./docs/TALK.md).

That is [docs/VOICES.md](./docs/VOICES.md). Voices, banks of sounds and faces
that are meant to be shared live in a repository of their own, a library,
where every voice carries its consent:

```sh
media library add owner/lighthouse-voices
media library voices lighthouse
media library clip ada recording.wav --by "Ada Marsh" --kind own    # in a library of your own: put a voice in
```

That is [docs/LIBRARIES.md](./docs/LIBRARIES.md).

A project of your own starts from a template:

```sh
media init workspace harbour-talks     # a pnpm workspace with a first deck in it
media init deck quarterly-review       # another deck, in its packages/
```

The templates are in
[media-engine-boilerplates](https://github.com/constructive-io/media-engine-boilerplates), in
the format `pgpm init` reads, and you can point it at your own. That is
[docs/PROJECTS.md](./docs/PROJECTS.md).

Settings are environment variables that start with `CONSTRUCTIVE_MEDIA_`; they
are listed in [`settings.py`](./python/constructive_media/settings.py).

Run the tests and the checks CI runs:

```sh
pnpm test                          # each module, in a throwaway database
pgpm test-packages --full-cycle    # deploy, verify, revert, deploy again
pnpm run audit:db                  # schema audit (security and performance)
PYTHONPATH=python python -m unittest discover -s tests -t .
```

The Python tests make their own media with `ffmpeg` and skip what your machine
cannot run. Those that touch the database run when
`CONSTRUCTIVE_MEDIA_TEST_DATABASE_URL` names a database with the modules
deployed, and roll back everything they do.

## Licensing

Everything in this repository is MIT licensed, and it stays that way:

- Code here is MIT. Dependencies are MIT, Apache-2.0 or BSD, and a test fails
  the build on one that is not.
- No copyleft code or dependency (GPL, AGPL, LGPL, MPL) is added here. Adapters
  for tools under those licenses live in a separate plugin repository, under
  their own license, and the engine works without any of them.
- `ffmpeg`, whisper.cpp and PostgreSQL are programs the engine runs, not code
  it contains. They keep their own licenses and you install them yourself.
- No model weights, voices, audio or footage are committed. A model's license
  applies to whoever downloads and runs it.

The full rule is in [docs/LICENSING.md](./docs/LICENSING.md), and every model the
engine knows by name, with its source and its license, is in
[docs/MODELS.md](./docs/MODELS.md).

## Related

- [conversation-format](https://github.com/constructive-io/conversation-format): the `.conv`
  screenplay format for multi-voice audio. This engine is an implementation of
  it: it reads a script and renders it. The grammar, the reference parser and
  a skill for writing scripts are there.
- [media-engine-plugins](https://github.com/constructive-io/media-engine-plugins):
  adapters for tools under copyleft licenses, each a package of its own that
  you choose to install.
- [media-engine-boilerplates](https://github.com/constructive-io/media-engine-boilerplates):
  what `media init` starts a project from: a pnpm workspace, and what goes in
  one. A narrated deck, a walkthrough, an animation, and a document that is
  read aloud, filmed as it scrolls, and printed: each a script and a page that
  `media film` turns into a video. And the template for a library of voices,
  sounds and faces in a repository of your own: `media init library NAME`.
- [pgpm](https://www.npmjs.com/package/pgpm): the Postgres package manager the
  schema modules are built with.

## Developing

### Auditing

[safegres](https://www.npmjs.com/package/safegres) grades the schema this workspace
deploys on two independent axes — security (grants, RLS, policy coverage and behavior)
and performance (predicates no index can serve, per-row function calls, foreign keys
without a covering index). It runs from the workspace root and needs no configuration
per module: every module under `packages/` is deployed into one ephemeral database and
that catalog is scanned, so a module joins the audit the moment `pgpm init` creates it.

```sh
# Audit: deploys every module into an ephemeral database and scans its catalog
pnpm run audit:db

# Accept today's performance findings as debt, so CI gates only on new ones
pnpm run audit:db:baseline

# Audit a database you already have instead
pnpm exec safegres audit --database your_db
```

The gates and the exposed surface to grade against live in `safegres.config.js`;
the run also writes JSON, markdown and SARIF reports to `safegres-reports/`.
`safegres-perf-baseline.json` is committed — it is the accepted performance debt
(today, only unscored notes about unindexed timestamp columns), and the only audit
file that carries state. CI runs the same audit
as a single `Database audit (safegres)` job.

Declaring `exposure` in the config is the first thing worth doing: until the audit
knows which schemas and roles your API reaches it has to assume the whole database
is, and caps the score at the `B` the gate allows.

### Using your own PostgreSQL

`pgpm docker start` is a convenience, not a requirement. If PostgreSQL (17+) is already
running on your machine, point pgpm at it and carry on — nothing else changes:

```sh
export PGHOST=localhost PGPORT=5432 PGUSER=postgres PGPASSWORD=yourpassword
pgpm admin-users bootstrap --yes   # once: creates the roles pgpm and tests expect
```

Don't run `eval "$(pgpm env)"` in that case: it prints the Docker container's defaults
(`postgres`/`password` on `localhost:5432`) and would overwrite yours. If you want the
Docker container *alongside* an existing server, give it another port and match it:
`pgpm docker start --port 5433` then `export PGPORT=5433`.

### Install scripts

`pnpm install` never asks you to approve build scripts here: every dependency that
wants to run code at install time already has a decision in `pnpm-policy.yaml`. If a
new dependency stops the install with `ERR_PNPM_IGNORED_BUILDS`, add it there — under
`allowBuilds` with a reason if it truly needs its script, or as `false` if it does not —
and run `pnpm run policy`. Don't use `pnpm approve-builds`; it writes outside the
managed block and `pnpm run policy:check` will fail.

### Working with an AI agent

Two skills are in `.agents/skills/`. `.agents/skills/constructive-media/` is how the engine is used:
setting it up, taking a `.conv` script to sound, taking footage to a cut, and what to
do when something goes wrong. Install it where your agent looks with:

```sh
npx skills add https://github.com/constructive-io/media-engine --skill constructive-media
```

`pgpm init workspace` installed the `pgpm` skill at `.agents/skills/pgpm/` — a guide to
pgpm's commands, the deploy/revert/verify pattern, plan files, dependencies, testing, and
CI. Agents that read `.agents/skills/` (Codex, Cursor, Gemini CLI, opencode, ...) pick it
up automatically; see [AGENTS.md](./AGENTS.md).

For an agent that looks elsewhere, either point it
at `.agents/skills/pgpm/SKILL.md` directly, or install the skill into the location it
expects:

```sh
npx skills add https://github.com/constructive-io/constructive --skill pgpm
```

### Prerequisites

- Node.js 22+
- pnpm
- Docker, or a local PostgreSQL 17+ you have superuser credentials for
- PostgreSQL client tools (`psql`)
- pgpm (`npm install -g pgpm`)

See [Prerequisites](https://constructive.io/learn/quickstart/prerequisites) for detailed setup instructions.

## 📚 Agent skills

* [Media Engine skills](https://github.com/constructive-io/media-engine/tree/main/.agents/skills): teach Claude Code or another coding agent to render scripts, stage video, and work with media libraries.

```bash
npx skills add constructive-io/media-engine
```

## Constructive Media ecosystem

Render scripts to audio and video, mix sound, and assemble footage into cuts.

### 🚀 Starters and related projects

* [Conversation Format](https://github.com/constructive-io/conversation-format): write `.conv` scripts using the spec, reference parser, and authoring skill.
* [Media Engine boilerplates](https://github.com/constructive-io/media-engine-boilerplates): scaffold a narrated deck, walkthrough, animation, or document.
* [Media Engine plugins](https://github.com/constructive-io/media-engine-plugins): optional adapters for tools under copyleft licenses.
* [Media Engine libraries](https://github.com/constructive-io/media-engine-libraries): find voices, sounds, and faces, including the [CC0 foley library](https://github.com/constructive-io/media-engine-library-foley).

### 📦 Key packages

* [constructive-media](https://github.com/constructive-io/media-engine/tree/main/python/constructive_media): the Python engine and the `media` CLI.
* [media-core](https://github.com/constructive-io/media-engine/tree/main/packages/media-core): personas, voices, assets, takes, and provenance.
* [media-video](https://github.com/constructive-io/media-engine/tree/main/packages/media-video): shots, transcripts, timelines, and editing decisions.
* [media-audio](https://github.com/constructive-io/media-engine/tree/main/packages/media-audio): speech synthesis, renders, and review.

## Credits

**🛠 Built by the [Constructive](https://constructive.io) team — creators of modular Postgres tooling for secure, composable backends. If you like our work, contribute on [GitHub](https://github.com/constructive-io).**

## Disclaimer

AS DESCRIBED IN THE LICENSES, THE SOFTWARE IS PROVIDED "AS IS", AT YOUR OWN RISK, AND WITHOUT WARRANTIES OF ANY KIND.

No developer or entity involved in creating this software will be liable for any claims or damages whatsoever associated with your use, inability to use, or your interaction with other users of the code, including any direct, indirect, incidental, special, exemplary, punitive or consequential damages, or loss of profits, cryptocurrencies, tokens, or anything else of value.
