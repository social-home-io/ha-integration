# ha-integration — Documentation

Reference material for the Home Assistant integration. The code is
the source of truth; these docs are derived from the current code
plus the spec — when they disagree, the code wins and the docs
should be fixed.

## Contents

- **[principles.md](./principles.md)** — Why the integration stays
  thin: a bridge to `socialhome-client`, Python 3.14 floor matching
  HA Core, CalVer releases, async everywhere, four GPS gates on the
  presence bridge.
- **[architecture.md](./architecture.md)** — Module layout, setup /
  unload sequence, config flow + options, coordinator error
  mapping, federation base URL push, federation inbox view,
  presence bridge.
- **[testing.md](./testing.md)** — 85 % branch-coverage gate,
  shared fixtures (`mock_client`, `mock_ws_manager`, …),
  patterns for testing the coordinator and presence gates, release
  flow.

## Where the spec lives

The authoritative specification is `spec_work.md` in the meta-repo.
Spec section references appear throughout as "§NN". This integration
is covered by §7; §6 covers the underlying client library
(`socialhome-client`).
