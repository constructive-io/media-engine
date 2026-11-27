# Footage to a cut

Every step is a `media` command that reads what the steps before it wrote to the
database, so each can be run again and none needs the others' files passed in.
Set up first, as [SKILL.md](../SKILL.md) describes, with `pip install ".[all]"`.

## Take stock of the recordings

```sh
media ingest ~/footage --project demo  # register every file, once
media shots                          # split each video into shots
media frames                         # save and measure sample frames
media signatures                     # a colour and motion signature per shot
media detect                         # find objects and people in the frames
```

## Hear what is said, and who says it

```sh
media vocabulary Zentrova "Ottoline Varga" --project demo  # names to listen for
media transcribe                     # what is said, sentence by sentence, word by word
media vocabulary Zentrova --heard-as "zen trova" --project demo  # what a name is misheard as
media mend                           # put right every mishearing on record, and keep what was heard
media relisten                       # transcribe the weakest recordings again, and keep what is better
media group                          # recordings that rolled together become a take
media match                          # a recorder with no clock finds its camera by what was said
media sync                           # place every recording on its take's timeline, and prove it
media takes                          # each take and how much of it is placed
media diarize                        # tell the voices in each recording apart
media speakers                       # each recording's voices, and a clip of each to listen to
media name interview voice1 "Ottoline Varga" --new  # say whose voice it is
```

A wrong name in a transcript is mended, not transcribed again: record what
the term is misheard as with `media vocabulary TERM --heard-as WORDS`, look
with `media mend --dry-run`, then `media mend`. It rewrites the lines and
their words and keeps what was heard, so `media mend --undo` puts all of it
back; run `media passages` again afterwards so the passages carry the mended
words. `media relisten` is for recordings the first recogniser did badly
with: `media relisten --dry-run` lists them, and with `--adapter` or a larger
model set it listens again and replaces a transcript only where the new one
is surer and found no less. A replaced transcript needs `media diarize` and
`media passages` again. `media transcribe --match NAME --from cleaned.wav`
listens to another file in place of one recording, and `--speaker NAME`
writes a name beside every line.

## Find what is worth using

```sh
media loudness                       # how loud each shot is against the minute around it
media coordination                   # whether the lit part of each shot moves with the rest of it
media audible                        # how well each line can be heard: the noise under it, and distortion
media laughter                       # where people laugh, as candidates to say yes or no to
media passages                       # cut each take's speech into passages worth using
media score                          # flag what to cut, then score every passage and shot
media rank interview --top 10        # the best of them, and how much each was measured
media judge                          # a text model reads the best of them (needs the `text` slot)
media index                          # make every passage findable by meaning (needs the `embedding` slot)
media search "the winter the boats came in close"
```

The four measures before `media passages` need no model, and `media score`
turns each into scores (`energy_lift`, `coordination`, `audio_snr`,
`undistorted`, `spectral_balance`, `laughter`). `media laughter` lists
candidates, each with a short id: listen, then say `media laughter --yes ID`
or `--no ID`. Once `media audible` has run, `media assemble` takes each
passage's sound from the cleanest recording measured. A recipe counts a
score once it weighs it: `media recipe NAME --like interview --weigh laughter=0.5`.

`media judge`, `media index` and `media search` talk to a server of your own, set with
`CONSTRUCTIVE_MEDIA_URL_TEXT` and `CONSTRUCTIVE_MEDIA_URL_EMBEDDING`. The rest
run on the machine.

## Decide, and keep the decisions

```sh
media script --out script.md         # the best passages as a document to mark keep, cut or trim
media script --read script.md        # store the marks as decisions
media keep 3f9a01c2                  # rule that a passage or shot is to be used
media cut 77b0de41                   # rule one out of every ranking and cut
media prefer 3f9a01c2 77b0de41       # which of two you would use
media fit --save taste               # a model of what you prefer, tested on footage held out from it
```

## Make the cut

```sh
media assemble talk --take interview  # the best passages as a cut, each seen and heard through the right recording
media cutaways talk-over --over talk  # other footage over some of it, the sound untouched
media compile reel --count 12        # or: shots that are good and unalike
media timeline talk-over             # the cut, clip by clip
media render talk-over               # the cut as a file to watch
media export talk-over --to handover/  # the cut for an editing program, pointed at the recordings themselves
```

`media <command> --help` describes each command and its options. The README has
what each step measures and how far to trust it.
