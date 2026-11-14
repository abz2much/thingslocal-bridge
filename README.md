<p align="center">
  <img src="assets/logo.svg" alt="ThingsLocal Bridge logo" width="112">
</p>

# ThingsLocal Bridge

Keep a Samsung SmartThings appliance working in Home Assistant without Samsung's
cloud. The bridge is a small local MQTT endpoint. You re-point the appliance at it,
and its readings show up in Home Assistant through the standard MQTT integration.
A dishwasher can also be controlled.

This is an experimental community project. It is not affiliated with Samsung or
SmartThings.

## What it does

- **Readouts** for a cooktop and a dishwasher, plus a generic profile. Values are
  what the appliance reports. A missing value shows as unavailable, never as zero.
- **Several appliances** on one endpoint, each with its own identity, registration
  and Home Assistant device.
- **Dishwasher controls**, all opt-in: program selection, Start, Pause, Resume,
  Cancel, Sanitize, Speed booster, Selected zone, auto door release and mute.
- **Dishwasher notifications** for device messages and cleaning reminders, through
  a blueprint.

Appliances are read-only until you turn a control on. See
[docs/controls.md](docs/controls.md).

## Does it work with my appliance?

The supported path is STDK MQTT with SoftAP EasySetup 1.x. A SmartThings label or
the words "Direct Connected" do not prove compatibility. There is no support for
OCF/CoAP, Zigbee, Z-Wave or Bluetooth onboarding.

| Appliance | Evidence |
| --- | --- |
| Samsung DW60A8050FS/EF dishwasher | Paired from a Mac. Readouts and every control above worked on 14 November 2026. |
| Samsung NZ64B5046KK cooktop | Onboarded, with twenty read-only entities verified. |
| Samsung dishwasher reporting DW8500-AM | Onboarded, with ten read-only entities verified. |
| Anything else | Untested. |

Full detail is in [docs/hardware-validation.md](docs/hardware-validation.md).

## What you need

- A domain name you own, and a valid TLS certificate for a hostname on it. The
  appliance rejects self-signed certificates. RapidSSL through DigiCert Global Root
  G2 was tested.
- Local DNS that points that hostname at the bridge's LAN IP. Nothing is exposed to
  the Internet.
- Home Assistant OS with an MQTT broker.
- A Samsung account, once, to fetch each appliance's setup keys.
- A Linux or macOS computer for setup and pairing.

## Quick start

1. Install the setup tool and run `init`.
2. Sign in to Samsung, add each appliance and fetch its keys.
3. Export the runtime bundle and copy it to Home Assistant's `share` folder.
4. Add this repository to Home Assistant, install the app and start it.
5. Point your local DNS at the bridge and run `check`.
6. Put the appliance in its pairing mode and run `pair`.

[![Add repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fabz2much%2Fthingslocal-bridge)

The exact commands are in [docs/setup.md](docs/setup.md).

## Things to know

- Certificate renewal is manual. Expiry shows as a diagnostic entity.
- The endpoint uses host port 8883, so turn off Mosquitto's host mapping for it.
- Samsung's cloud monitoring may stop once an appliance points at your bridge.
- The protocol checks the appliance's IP and token claims but not the token
  signature. Keep the endpoint on a trusted private network.
- Setup secrets stay on the setup computer. Never put the workspace, keys or the
  runtime bundle in a public repository.

## Documentation

| Page | What is in it |
| --- | --- |
| [docs/setup.md](docs/setup.md) | Step by step install and pairing, including macOS |
| [docs/controls.md](docs/controls.md) | Dishwasher controls, commands and results |
| [docs/configuration.md](docs/configuration.md) | `config.json`, extra attributes, hiding entities |
| [docs/notifications.md](docs/notifications.md) | The notification blueprint |
| [docs/recovery.md](docs/recovery.md) | Backups, rollback and moving a registry |
| [docs/hardware-validation.md](docs/hardware-validation.md) | What was tested, and what was not |
| [CHANGELOG.md](CHANGELOG.md) | Release notes |

## Tests

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

The tests use fake appliances and do not prove compatibility with real ones.
Hardware results are recorded in the validation page.

## Credits and licence

This project continues [jmguzik's ThingsLocal Bridge](https://github.com/jmguzik/thingslocal-bridge),
which continues [LocalThings](https://codeberg.org/purplesym/localthings) by
[purplesym (Lars)](https://codeberg.org/purplesym) and its contributors. The
protocol research is at [6xq.net/localthings](https://6xq.net/localthings/). The
original MQTT source is included unmodified, with its copyright notices.

Licensed under AGPL-3.0-or-later. See [LICENSE](LICENSE) and
[UPSTREAM.md](UPSTREAM.md) for sources and what changed.
