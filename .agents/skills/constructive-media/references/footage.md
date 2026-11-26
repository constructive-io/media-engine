# Footage to a cut

Every step is a `me` command that reads what the steps before it wrote to the
database, so each can be run again and none needs the others' files passed in.
Set up first, as [SKILL.md](../SKILL.md) describes, with `pip install ".[all]"`.

## Take stock of the recordings

```sh
me ingest ~/footage --project demo   # register every file, once
me shots                             # split each video into shots
me frames                            # save and measure sample frames
me signatures                        # a colour and motion signature per shot
me detect                            # find objects and people in the frames
```

## Hear what is said, and who says it

```sh
me vocabulary Zentrova "Ottoline Varga" --project demo   # names to listen for
me transcribe                        # what is said, sentence by sentence, word by word
me group                             # recordings that rolled together become a take
me match                             # a recorder with no clock finds its camera by what was said
me sync                              # place every recording on its take's timeline, and prove it
me takes                             # each take and how much of it is placed
me diarize                           # tell the voices in each recording apart
me speakers                          # each recording's voices, and a clip of each to listen to
me name interview voice1 "Ottoline Varga" --new   # say whose voice it is
```

## Find what is worth using

```sh
me passages                          # cut each take's speech into passages worth using
me score                             # flag what to cut, then score every passage and shot
me rank interview --top 10           # the best of them, and how much each was measured
me judge                             # a text model reads the best of them (needs the `text` slot)
me index                             # make every passage findable by meaning (needs the `embedding` slot)
me search "the winter the boats came in close"
```

`me judge`, `me index` and `me search` talk to a server of your own, set with
`CONSTRUCTIVE_MEDIA_URL_TEXT` and `CONSTRUCTIVE_MEDIA_URL_EMBEDDING`. The rest
run on the machine.

## Decide, and keep the decisions

```sh
me script --out script.md            # the best passages as a document to mark keep, cut or trim
me script --read script.md           # store the marks as decisions
me keep 3f9a01c2                     # rule that a passage or shot is to be used
me cut 77b0de41                      # rule one out of every ranking and cut
me prefer 3f9a01c2 77b0de41          # which of two you would use
me fit --save taste                  # a model of what you prefer, tested on footage held out from it
```

## Make the cut

```sh
me assemble talk --take interview    # the best passages as a cut, each seen and heard through the right recording
me cutaways talk-over --over talk    # other footage over some of it, the sound untouched
me compile reel --count 12           # or: shots that are good and unalike
me timeline talk-over                # the cut, clip by clip
me render talk-over                  # the cut as a file to watch
me export talk-over --to handover/   # the cut for an editing program, pointed at the recordings themselves
```

`me <command> --help` describes each command and its options. The README has
what each step measures and how far to trust it.
