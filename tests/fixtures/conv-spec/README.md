# conv-spec fixtures

The conformance files of the [`.conv` format](https://github.com/constructive-io/conv-spec),
copied as they are from its `__fixtures__` folder at commit `615c4ac`. They are
MIT licensed; the license is beside this file.

- `scenes/X.conv` must parse to exactly `scenes/X.json`.
- `errors/X.conv` must report exactly the problems in `errors/X.errors.json`.
- `banks/` holds the sound banks the scripts include.

Any implementation of the format passes the same files. Do not edit them here:
when the format changes, copy them again.
