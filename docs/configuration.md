# Explicit configuration and read-only attribute mappings

The private workspace contains `config.json`. `init` and `add-device` generate it;
no owner-specific address, domain, SSID, model or serial is compiled into the app.

Several appliances share the same `broker` hostname/certificate and MQTT broker.
Each entry in `devices` has a unique `id`, reserved `ip`, registration `label` and
own identity (`manufacturer`, `setup_id`, `serial`, `model`). IDs are not secrets;
identities and configuration files remain private.

The supported built-in profiles are `cooktop`, `dishwasher`, and `generic`.
`generic` uses explicit standard read-only mappings (for example switch state,
temperature, humidity, battery, contact, power and energy where scalar and supplied
with appropriate units). It does not publish all arbitrary incoming attributes.

For an additional scalar attribute, opt in to the exact path:

```json
{
  "entities": [
    {
      "component": "main",
      "capability": "vendor.exampleStatus",
      "attribute": "status",
      "platform": "sensor",
      "name": "Custom status"
    }
  ]
}
```

Supported custom-mapping platforms are `sensor` and `binary_sensor` only. Custom
attribute mappings cannot create command topics. Binary mappings accept boolean values or known on/off,
open/closed and locked/unlocked strings; unknown values become unavailable.
Complex objects/arrays are not automatically converted into scalar readings.

Optional keys include `unit_of_measurement`, `device_class` and `entity_category`.
These must match the actual attribute and Home Assistant's entity schema. The
private catalog records observed paths/types/units without copying values.

To hide virtual or unverified zone components by default, set e.g.:

```json
{"optional_components": ["burner-05", "burner-06"]}
```

This is an installation choice, not a universal assumption about every cooktop.
Keep the installation ID stable for ordinary updates. Use a new ID/workspace for
a deliberately separate clean test, so old discovery entities cannot impersonate
newly reported data.

## Explicit dishwasher program control

Only the dishwasher profile can opt in to the following device-level setting:

```json
{"controls": {"dishwasher_course": true}}
```

The flag must be a JSON boolean. Unknown control names and enabling this control
for a cooktop/generic profile are rejected. The flag is included in private runtime
exports. New and existing installations remain read-only when it is absent/false.

This adds a standard Home Assistant MQTT select with live `supportedCourses`
options, a reported state and an independent readiness/availability topic. The
selector sends only `setWashingCourse`, with one explicitly reported program as
its argument. No arbitrary command or topic can be configured through this flag.
See the README for readiness checks, confirmation and unknown-outcome handling.

## Remote control status and remote start

Dishwasher profiles expose `remoteControlStatus` / `remoteControlEnabled` as a
read-only Home Assistant binary sensor, **Remote control enabled**. It accepts the
values `true` and `false` (as text or boolean). Anything else is unavailable. The
attribute name was confirmed on a real DW60A8050FS/EF (14 November 2026). The
dishwasher reports it only after remote control is switched at the machine.

Remote start is a separate opt-in, off by default:

```json
{"controls": {"dishwasher_start": true}}
```

The flag must be a JSON boolean. It is rejected for cooktop and generic profiles.
It is independent of `dishwasher_course`. It adds a Home Assistant MQTT button
**Start** and a diagnostic **Start result**. Removing the flag and restarting
removes both.

Pressing the button sends exactly one command to that dishwasher over its existing
local connection:

```json
{"commands": [{"component": "main", "capability": "dishwasherOperatingState",
               "command": "setMachineState", "arguments": ["run"], "id": "<new UUID>"}]}
```

The bridge refuses, and sends nothing, unless all of these hold:

- the flag is on and the device is a dishwasher;
- the appliance is connected and subscribed to its command topic;
- the last `remoteControlEnabled` report in this connection was true;
- no start was already sent in this connection (see below).

A refusal is logged and shown in **Start result**: `control_disabled`,
`not_connected`, `remote_control_unknown`, `remote_control_disabled`,
`already_sent`, `session_changed`. Remote status is forgotten on every new
connection, so a reconnect needs a fresh report before start is allowed.
After a send, the result is `sent_unconfirmed`. The appliance does not
acknowledge the command, so the bridge cannot say the cycle started. A further
press is refused until the dishwasher reports a new operating state.

Press messages use QoS 0, clean sessions and `retain: false`. Retained replays and
any payload other than `PRESS` are rejected. Nothing is retried or queued by the
bridge.

**Confirmed on one DW60A8050FS/EF (14 November 2026).** The bridge sends the
standard SmartThings command `dishwasherOperatingState.setMachineState("run")`
over the local connection, and the dishwasher started a wash. The alternative,
`samsungce.dishwasherOperation.start`, is documented upstream without its
arguments and was not needed. Other models are unconfirmed. The dishwasher's own
remote control setting must be armed at the machine, and starting an appliance
unattended is the owner's responsibility.

## Dishwasher option controls

```json
{"controls": {"dishwasher": ["sanitize", "speed_booster", "selected_zone", "door_release", "mute", "pause", "resume", "cancel"]}}
```

The list is optional, may hold any of the names once each (`pause`, `resume` and
`cancel` are buttons and also belong in it), and is accepted only for dishwasher
profiles. Each name adds one Home Assistant entity (a switch, a select for
`selected_zone`, or a button) plus the plain topics described in the README,
and one shared **Control result** diagnostic. Results: `confirmed`, `pending`,
`not_ready`, `busy`, `unchanged`, `unsupported_value`, `not_settable`,
`timeout_unknown`, `send_failed_unknown`, `session_changed`, `disconnected`.
A timeout means the outcome is unknown: the control stays unavailable until the
dishwasher next reports that attribute or reconnects.

Commands (proven on a DW60A8050FS/EF, 14 November 2026): the three options use
`samsungce.dishwasherWashingOptions` `setOptions` with a one-key dictionary;
door release uses `samsungce.autoDoorRelease` `enable` or `disable`; mute uses
`audioMute` `mute` or `unmute`; pause and resume use `dishwasherOperatingState`
`setMachineState` (`["pause"]`, `["run"]`); cancel uses `samsungce.dishwasherOperation`
`cancel`. The buttons need remote control reported on and an operating state they
allow (`samsungce.dishwasherOperation` `operatingState`: pause while `running`,
resume while `paused`, cancel while `running` or `paused`). The single setters are
ignored by this model.
Changing the list needs a config change and an app restart; changing the app
code needs a rebuild of the local app.

First test of remote start, safest order: enable the flag, load the machine with nothing
harmful, watch with the machine in view, check **Remote control enabled** follows
the machine's setting, press Start with remote control off (expect
`remote_control_disabled`), then arm it and press once. Check the machine and the
bridge log. Do not automate it until this has worked by hand.
