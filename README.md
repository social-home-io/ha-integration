# ha-integration

Home Assistant custom integration for [Social Home](https://github.com/social-home-io/core).
Installed via [HACS](https://hacs.xyz/). Spec: §7 of `spec_work.md` in the
Social Home meta-repo.

## What it does

The integration connects one Home Assistant instance to one Social Home
instance. It takes an instance URL + API token (or picks those up
automatically from the Supervisor when Social Home is installed as an HA
App) and opens a shared HTTP client + polling coordinator that the
individual platforms (sensor, calendar, notify, …) will plug into.

This initial cut ships the **integration skeleton only** — config flow,
setup/unload, options, and the coordinator. No entities are registered
yet; platform modules land in follow-up PRs.

## Install

Add the repo to HACS as a custom integration, then install *Social Home*
from the HACS integrations list and restart HA. Add the integration from
*Settings → Devices & services → Add integration → Social Home*.

In App mode the setup runs automatically via Supervisor discovery — no
URL or token is typed. In standalone mode the flow asks for the instance
URL and an API token minted in *Settings → Security* on the Social Home
web UI.

## Develop

```sh
pip install -e .[dev]
pre-commit install
pytest
```

Tests stub out the Home Assistant core plumbing; no live HA instance
required.

## License

[Mozilla Public License 2.0](LICENSE).
