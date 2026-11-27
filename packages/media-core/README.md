# media-core

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
  The shared spine of Constructive Media: personas, voices, assets, takes and episodes, and the record of what was derived from what.
</p>

Schema: `media`. Part of [media-engine](https://github.com/constructive-io/media-engine).

## Developing

This module was generated with `pgpm init`. For a complete guide on creating and testing database modules, see [Creating Your First Module](https://constructive.io/learn/modular-postgres/creating-first-module).

```sh
# Install dependencies
pnpm install

# Run tests
pnpm test

# Run tests in watch mode
pnpm test:watch

# Deploy to a database
pgpm deploy --database your_db --createdb --yes
```

## Auditing

The workspace audits this module's schema together with every other module in
`packages/`, from the workspace root — there is nothing to configure here:

```sh
# From the workspace root: deploys every module into an ephemeral database and
# scans its catalog (security + performance grades)
pnpm run audit:db
```

The gates and the exposed surface to grade against live in the workspace's
`safegres.config.js`; see the workspace README for the rest.

## Credits

**🛠 Built by the [Constructive](https://constructive.io) team — creators of modular Postgres tooling for secure, composable backends. If you like our work, contribute on [GitHub](https://github.com/constructive-io).**

## Disclaimer

AS DESCRIBED IN THE LICENSES, THE SOFTWARE IS PROVIDED "AS IS", AT YOUR OWN RISK, AND WITHOUT WARRANTIES OF ANY KIND.

No developer or entity involved in creating this software will be liable for any claims or damages whatsoever associated with your use, inability to use, or your interaction with other users of the code, including any direct, indirect, incidental, special, exemplary, punitive or consequential damages, or loss of profits, cryptocurrencies, tokens, or anything else of value.
