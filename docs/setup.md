# Setup guide

This guide takes you from nothing to one paired appliance. Run the commands on
the Linux setup computer unless a step says otherwise. Replace example paths with
your own. A Mac can do the pairing step: see [Pairing from a Mac](#pairing-from-a-mac).

## What you need

- A domain name you own, and control of its DNS records.
- A valid TLS certificate for the broker hostname, with its private key and
  intermediate chain. RapidSSL was tested, through `RapidSSL TLS RSA CA G1` to
  `DigiCert Global Root G2`, with an RSA 2048-bit key. The tested cooktop rejected a
  self-signed certificate. Other issuers, roots and key types are untested.
- A local DNS override from that name to the endpoint's reserved LAN IP. Nothing
  needs to be public, and no port needs forwarding. One hostname and certificate
  serve every compatible appliance.
- Each appliance's SmartThings QR data, or its verified manufacturer code, setup ID
  and serial. There is no default or guessed setup ID.
- A Samsung account for the first EasySetup material, and a WPA2 Wi-Fi network the
  appliance can see. Samsung credentials stay on the setup computer. Routine
  monitoring does not call the SmartThings API.
- A setup computer with Python 3.11 or later and OpenSSL 3. On Linux you also need
  NetworkManager, a Wi-Fi adapter and a systemd user session. Node.js and npm are
  needed for the optional official Samsung login.
- Home Assistant OS, access to its share and local-apps folders, and a working
  MQTT integration. The official Mosquitto app is a good choice.
- DHCP reservations for the endpoint and every appliance.

Certificate renewal is manual. Renew before it expires, replace the private files
and restart the app. Expiry shows up as a diagnostic entity in Home Assistant.

## 1. Install the setup CLI

```sh
git clone https://github.com/abz2much/thingslocal-bridge.git
cd thingslocal-bridge
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[setup]'
.venv/bin/thingslocal --workspace ~/.config/thingslocal init
```

The wizard asks for your domain, the endpoint IP, certificate and key paths, MQTT
credentials, Wi-Fi credentials and timezone. Passwords use a hidden prompt. Private
files are mode 0600 inside a mode 0700 workspace. Never put the workspace or an
exported runtime bundle in a public repository.

## 2. Sign in to Samsung and add appliances

```sh
.venv/bin/thingslocal --workspace ~/.config/thingslocal login --install-cli
.venv/bin/thingslocal --workspace ~/.config/thingslocal cloud-devices
.venv/bin/thingslocal --workspace ~/.config/thingslocal add-device
.venv/bin/thingslocal --workspace ~/.config/thingslocal keys YOUR_DEVICE_ID
```

`login --install-cli` downloads the pinned official SmartThings CLI login support
and opens Samsung's browser flow. If no browser appears, open the printed
`http://localhost:PORT/start` link on the same computer. The callback only listens
on loopback. A generic Home Assistant token may lack the identity EasySetup needs.
If the API says so, use the official Samsung user login.

`add-device` takes the URL from your QR code, or a manual identity. You choose a
unique ID, a reserved IP, a display name, a registration label and a profile
(`cooktop`, `dishwasher` or `generic`). Repeat `add-device` and `keys` for each
appliance. You can change a display name later without touching the identity.

`keys` makes a new cloud-assisted key request and checks the full serial hash in
the response. Keep the `setup-material` files private. If you already own the
material, `keys YOUR_DEVICE_ID --import-file FILE` imports it. That is not a fresh
key request, so do not describe a test that used it as one.

## 3. Prepare Home Assistant and the runtime bundle

Install your MQTT broker and the standard MQTT integration. Give the bridge its
own broker login. It needs to publish to its `thingslocal/...` state topics and the
`homeassistant/.../config` discovery topics, and to subscribe to
`homeassistant/status`. Each dishwasher also subscribes to its own
`thingslocal/DEVICE_IDENTIFIER/notification_ack` topic. Controls add their own
command topics (see [controls.md](controls.md)).

The appliance endpoint uses host TCP port 8883. Turn off Mosquitto's host-side
8883 mapping, or the two will clash. If both run inside Home Assistant, you can
turn off all Mosquitto host ports and use `core-mosquitto:1883` on the internal
network.

```sh
.venv/bin/thingslocal --workspace ~/.config/thingslocal export-runtime \
  --output /path/to/empty-private-export \
  --target-workspace /share/thingslocal
```

The export holds only the runtime configuration, your certificate and your private
key. It does not copy Wi-Fi credentials, Samsung tokens, EasySetup keys or an old
registration. The target path must match the app's `workspace` option.

Copy the exported files into Home Assistant's `share/thingslocal` folder and keep
them private. Do not put them in `www`, the app source or a Git checkout.

## 4. Install the Home Assistant app

Add this repository under Settings, Apps, Install app, Repositories:

```text
https://github.com/abz2much/thingslocal-bridge
```

[![Add repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fabz2much%2Fthingslocal-bridge)

Install ThingsLocal Bridge from it. Set its `workspace` option to the runtime
folder from step 3, start it, and turn on start on boot and the watchdog. HACS is
not needed.

To run a local copy instead, copy only the `thingslocal_bridge/` folder into
`local_apps/thingslocal_bridge` on Home Assistant. It is shared over Samba, or
appears as `/local_apps` in the Terminal & SSH app. Older releases call it
`addons`. Then use Check for updates and install it from Local apps. Code changes
in a local app need a **Rebuild**. A restart only picks up configuration changes.
See the [official local-app tutorial](https://contributors.home-assistant.io/apps/first-app/).

The app can run before any appliance is paired. Its log should say
`thingslocal_ready`. Certificate diagnostics appear first. Other entities appear as
their attributes are reported.

## 5. Set up local DNS and check the route

Make your broker hostname resolve only to the endpoint's private IP, and hand that
DNS out through DHCP. Then run:

```sh
.venv/bin/thingslocal --workspace ~/.config/thingslocal check
```

The check covers the hostname, the certificate chain, the key match, expiry, the
exact private DNS answer and TCP reachability of the endpoint. It rejects any extra
IPv6 (AAAA) record, because the endpoint is IPv4 only. It trusts DigiCert Global
Root G2. Another trust bundle needs explicit configuration and its own device test.

## 6. Pair an appliance

Put the appliance into its AP mode using the manufacturer's steps for that model.
Button sequences differ. On the DW60A8050FS/EF, hold Smart Control alone for three
seconds. Keep the computer on your normal network for now.

```sh
.venv/bin/thingslocal --workspace ~/.config/thingslocal scan YOUR_DEVICE_ID
.venv/bin/thingslocal --workspace ~/.config/thingslocal pair YOUR_DEVICE_ID
```

Add `--interface INTERFACE` to `pair` to pick a Wi-Fi adapter. The tool checks the
AP's identity and full serial hash before it sends any Wi-Fi credentials. It then
runs encrypted EasySetup, restores the computer's connection and removes its
temporary AP profile. A separate 120-second systemd timer restores the connection
if the process crashes. A timeout after a configuration write is reported as
unknown, not as unchanged.

Pairing points the appliance at your endpoint instead of its old broker, so
Samsung's cloud monitoring may stop. By default the bridge only reads.

Repeat for each appliance. To add one to an existing install, export the updated
configuration into a new empty directory, copy it over the runtime files, keep the
Home Assistant `state/` folder, restart the app and then pair.

### Pairing from a Mac

On a Mac, `pair` uses a manual join. The tool does not change your Wi-Fi. It needs
no `sudo`, no extra packages and no Location permission. It worked once on a real
Mac with a DW60A8050FS/EF (14 November 2026). Other Macs and models are untested.

1. Put the appliance in pairing mode.
2. Run `pair YOUR_DEVICE_ID`. With more than one Wi-Fi interface, add
   `--interface en0` or the right name.
3. Join the appliance's setup network from the Wi-Fi menu. Its name contains `_E4`
   or `_E5`. The password is `1111122222`. Ignore the "no internet" warning.
4. Press Enter in the terminal.
5. The tool checks that the router answers on port 8888. If not, it says so and
   sends nothing.
6. After pairing, the appliance's network disappears. The tool waits up to 60
   seconds for your normal network. If it does not return, pick it yourself.

`scan` does not work on a Mac. Look for the appliance's network in the Wi-Fi menu.
