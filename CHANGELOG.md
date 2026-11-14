# Changelog

## Unreleased

- macOS support for the `pair` step (you join the appliance's setup Wi-Fi from
  the menu bar). `scan` is not supported on a Mac.
- Dishwasher remote start: an opt-in Start button and a "Remote control enabled"
  sensor, guarded so it sends only when remote control is reported on.
- A small control engine and eight opt-in dishwasher controls, listed in
  `controls.dishwasher`: Sanitize, Speed booster, Selected zone, auto door release
  and mute, plus Pause, Resume and Cancel buttons. Each is available only in the
  right state, allows one pending command at a time, and is confirmed by the
  dishwasher's next report. Plain MQTT topics and a "Control result" diagnostic
  are published alongside the Home Assistant entities.
- Proven on one DW60A8050FS/EF (14 November 2026). Other models are unconfirmed.
- Documentation updated, including what was and was not proven on real hardware.

## 0.2.0 — 2026-11-10 — Experimental public release

Continued work on **LocalThings by purplesym (Lars)** and the LocalThings
contributors. The original MQTT implementation is included unmodified under
AGPL-3.0-or-later. See UPSTREAM.md for the source and protocol documentation.

- Home Assistant app and setup CLI for compatible STDK MQTT / EasySetup 1.x devices.
- Multiple appliances with separate registrations, sessions and HA identities.
- Cooktop and dishwasher telemetry profiles; read-only by default.
- Explicitly enabled dishwasher program selection, confirmed by device reports.
- Dishwasher attention messages, fresh-count cleaning reminders and a notification
  blueprint for the HA panel and owner-selected notify services.
- Durable notification FIFO, acknowledgement-based deduplication and recovery docs.
- Tested onboarding for Samsung NZ64B5046KK and a dishwasher reporting DW8500-AM.

Each installation requires its own domain and valid compatible TLS certificate.
RapidSSL through DigiCert Global Root G2 was tested. Certificate renewal is manual.
Other appliances, real fault notifications and real cleaning reminders remain
unverified beyond the scope recorded in docs/hardware-validation.md.
