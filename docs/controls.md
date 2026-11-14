# Dishwasher controls

Every appliance is read-only until you opt in. All controls are dishwasher only, and
each is set per device in its private `config.json`. After a change, export the
runtime configuration again and restart the app, keeping the `state/` folder.

Everything here was proven on one DW60A8050FS/EF on 14 November 2026. Other
models are untested. The dishwasher never acknowledges a command. A change counts
as done only when the next report from the machine shows it.

## How the controls behave

- Only one command per appliance can be pending.
- Nothing is queued, retried or saved for reconnection.
- Command topics use QoS 0 and `retain: false`. Retained replays are rejected.
- A timeout means the outcome is unknown. The control stays unavailable until the
  dishwasher next reports that attribute, or reconnects.
- A **Control result** diagnostic shows the last result: `confirmed`, `pending`,
  `not_ready`, `busy`, `unchanged`, `unsupported_value`, `not_settable`,
  `timeout_unknown`, `send_failed_unknown`, `session_changed` or `disconnected`.

## Program selection

```json
{"controls": {"dishwasher_course": true}}
```

Adds a **Washing program** dropdown and a **Program selection result** diagnostic.
The dropdown lists only the programs the machine reports. It works in the normal
Home Assistant UI on a computer or phone. Choosing a program does not start a wash.

It is available only when the machine is on, in operating state `ready`, with job
state `none`, a known selected program, fresh data and a live command
subscription. It sends one command: `samsungce.dishwasherWashingCourse.setWashingCourse`.
A request waits up to ten seconds for a matching report.

## Remote start

```json
{"controls": {"dishwasher_start": true}}
```

Adds a **Start** button and a **Start result** diagnostic. A press sends one
command, `dishwasherOperatingState.setMachineState("run")`, and only if the
machine has reported remote control as on during the current connection. Remote
control must be armed at the machine. Starting an appliance unattended is your
responsibility.

The result is `sent_unconfirmed`, never "started", because there is no
acknowledgement. Refusals are `control_disabled`, `not_connected`,
`remote_control_unknown`, `remote_control_disabled`, `already_sent` and
`session_changed`. A second press is refused until the machine reports a new
operating state. Anything other than `PRESS` is rejected.

The **Remote control enabled** sensor is on every dishwasher profile. The machine
reports it only after you change it at the machine, so after an app restart it
stays unknown until the next change.

To test it safely, enable the flag and watch the machine. Check the sensor follows
the machine's setting. Press Start with remote control off and expect
`remote_control_disabled`. Then arm it and press once. Do not automate it until it
has worked by hand.

## Option controls

List the controls you want by name:

```json
{"controls": {"dishwasher": ["sanitize", "speed_booster", "selected_zone", "door_release", "mute", "pause", "resume", "cancel"]}}
```

| Name | Entity | What it sends |
| --- | --- | --- |
| `sanitize` | switch | `setOptions` with `{"sanitize": true or false}` |
| `speed_booster` | switch | `setOptions` with `{"speedBooster": true or false}` |
| `selected_zone` | select (`all`, `lower`) | `setOptions` with `{"selectedZone": "..."}` |
| `door_release` | switch | `autoDoorRelease` `enable` or `disable` |
| `mute` | switch | `audioMute` `mute` or `unmute` |
| `pause` | button | `dishwasherOperatingState` `setMachineState` `["pause"]` |
| `resume` | button | `dishwasherOperatingState` `setMachineState` `["run"]` |
| `cancel` | button | `samsungce.dishwasherOperation` `cancel` (the wash ends and the machine drains) |

Each name may appear once. The three option controls work only while the machine is
on and idle, and only for values the current programme allows. Door release and
mute need only a live connection. This model reports them only after they change,
so they show no state until the first change. Options worked with remote control
off. The single setters such as `setSanitize` are ignored by this model.

The buttons need remote control reported on, and the right operating state: pause
while running, resume while paused, cancel while running or paused. A press counts
as done when the state reaches paused, running or ready. Because the machine
reports remote control only after a change at the machine, the buttons stay
unavailable after an app restart until you toggle it. Pause, resume and cancel were
proven by command. The buttons themselves are newer. You may want Home Assistant to
ask for confirmation before Cancel.

Plain MQTT topics are published too, for other tools:

- State: `<namespace>/<device id>/<name>`, with `ON` or `OFF`, or the option text.
- Commands: `<namespace>/<device id>/<name>/set`, with `ON`, `OFF`, the option
  text, or `PRESS` for buttons. Payloads are at most 64 bytes.

Delayed start (`startLater`, in seconds) works on the machine but is not a control
yet. Power off does not work locally.

## Notifications

Dishwasher profiles also report device messages, an attention flag, the cleaning
count and threshold, and a derived cleaning recommendation. A recommendation needs
a fresh local cycle count and a positive threshold. The
[notification blueprint](../blueprints/dishwasher_notifications.yaml) sends these to
the Home Assistant panel and any notify services you choose. See
[notifications.md](notifications.md).
