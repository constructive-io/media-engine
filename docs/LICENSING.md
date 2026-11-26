# Licensing rule

This repository is MIT licensed. The rule below keeps it that way.

## What may live here

| Thing | Allowed |
| --- | --- |
| Code written for this repository | MIT |
| Code copied in from elsewhere | MIT or Apache-2.0 only, with its notice kept |
| Dependencies the engine needs to run | MIT or Apache-2.0 only |
| Copyleft code or dependencies (GPL, AGPL, LGPL) | Never |
| Non-commercial or research-only code | Never |
| Model weights, voices, audio, images, footage | Never, under any license |

A dependency under another permissive license (BSD, ISC and similar) needs a
maintainer's sign-off and a line in the table at the bottom of this file.

## Adapters and plugins

The engine reaches every model and every effect through an adapter. Each slot
has a default that meets the rule above, so the engine runs with nothing extra
installed.

An adapter for a tool that does not meet the rule lives in a separate plugin
repository under that tool's license. The engine never imports a plugin by
name. It discovers the ones you have installed.

## Models

The engine ships no weights. Installing a model is your choice, and that
model's license binds you, not this repository. The model list names each
model's license next to it so the choice is an informed one.

## Programs the engine calls

Programs the engine runs as a separate process, such as `ffmpeg` and
PostgreSQL, are not part of this repository and keep their own licenses.

## Sign-offs

| Dependency | License | Why it is needed | Signed off by |
| --- | --- | --- | --- |
| (none yet) | | | |
