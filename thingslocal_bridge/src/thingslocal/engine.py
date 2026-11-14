"""Table-driven dishwasher controls. Every row was proven on a real DW60A8050FS/EF on 14 Nov 2026."""
import asyncio
from dataclasses import dataclass
import re
from typing import Callable

from .controls import ControlRejected

OPTIONS = 'samsungce.dishwasherWashingOptions'
DOOR = 'samsungce.autoDoorRelease'
TOKEN = re.compile(r'[A-Za-z0-9_.-]{1,64}\Z')


@dataclass(frozen=True)
class ControlSpec:
    key: str
    name: str
    platform: str                 # 'switch', 'select' or 'button'
    state: tuple                  # (capability, attribute) the control reads and confirms from
    read: Callable                # raw report value -> bool or str, or None when malformed
    command: Callable             # value -> (capability, command, arguments)
    guard: str                    # 'idle' or 'connected'
    icon: str
    options: Callable = None      # selects: raw report value -> list of option strings
    settable: Callable = None     # switches with a settable list: raw report value -> list of bool, or None
    allowed: tuple = None         # buttons: operating states in which a press is allowed
    target: str = None            # buttons: the operating state that confirms a press
    needs_remote: bool = False    # available only while remote control is reported on


def _dictionary_value(raw):
    return raw.get('value') if isinstance(raw, dict) else None


def _option_flag(raw):
    value = _dictionary_value(raw)
    return value if isinstance(value, bool) else None


def _option_text(raw):
    value = _dictionary_value(raw)
    return value if isinstance(value, str) and TOKEN.fullmatch(value) else None


def _settable_flags(raw):
    items = raw.get('settable') if isinstance(raw, dict) else None
    return list(items) if isinstance(items, list) and all(isinstance(item, bool) for item in items) else None


def _settable_texts(raw):
    items = raw.get('settable') if isinstance(raw, dict) else None
    if not isinstance(items, list) or not all(isinstance(item, str) and TOKEN.fullmatch(item) for item in items):
        return []
    return list(dict.fromkeys(items))


def _set_option(name):
    return lambda value: (OPTIONS, 'setOptions', [{name: value}])


def _flag(raw):
    return raw if isinstance(raw, bool) else None


def _text(raw):
    return raw if isinstance(raw, str) and TOKEN.fullmatch(raw) else None


def _muted(raw):
    return {'muted': True, 'unmuted': False}.get(raw) if isinstance(raw, str) else None


CONTROLS = (
    ControlSpec('sanitize', 'Sanitize', 'switch', (OPTIONS, 'sanitize'), _option_flag,
                _set_option('sanitize'), 'idle', 'mdi:shimmer', settable=_settable_flags),
    ControlSpec('speed_booster', 'Speed booster', 'switch', (OPTIONS, 'speedBooster'), _option_flag,
                _set_option('speedBooster'), 'idle', 'mdi:fast-forward', settable=_settable_flags),
    ControlSpec('selected_zone', 'Selected zone', 'select', (OPTIONS, 'selectedZone'), _option_text,
                _set_option('selectedZone'), 'idle', 'mdi:dishwasher', options=_settable_texts),
    ControlSpec('door_release', 'Auto door release', 'switch', (DOOR, 'autoDoorReleaseEnabled'), _flag,
                lambda value: (DOOR, 'enable' if value else 'disable', []), 'connected', 'mdi:door-open'),
    ControlSpec('mute', 'Mute beeps', 'switch', ('audioMute', 'mute'), _muted,
                lambda value: ('audioMute', 'mute' if value else 'unmute', []), 'connected', 'mdi:volume-off'),
)
CONTROLS += (
    ControlSpec('pause', 'Pause', 'button', ('samsungce.dishwasherOperation', 'operatingState'), _text,
                lambda value: ('dishwasherOperatingState', 'setMachineState', ['pause']), 'connected',
                'mdi:pause', allowed=('running',), target='paused', needs_remote=True),
    ControlSpec('resume', 'Resume', 'button', ('samsungce.dishwasherOperation', 'operatingState'), _text,
                lambda value: ('dishwasherOperatingState', 'setMachineState', ['run']), 'connected',
                'mdi:play-circle-outline', allowed=('paused',), target='running', needs_remote=True),
    ControlSpec('cancel', 'Cancel', 'button', ('samsungce.dishwasherOperation', 'operatingState'), _text,
                lambda value: ('samsungce.dishwasherOperation', 'cancel', []), 'connected',
                'mdi:stop', allowed=('running', 'paused'), target='ready', needs_remote=True),
)
KEYS = tuple(item.key for item in CONTROLS)


POWER = ('switch', 'switch')
OPERATION = ('samsungce.dishwasherOperation', 'operatingState')
JOB = ('samsungce.dishwasherJobState', 'dishwasherJobState')
REMOTE = ('remoteControlStatus', 'remoteControlEnabled')
GUARD_KEYS = (POWER, OPERATION, JOB)


class EngineControl:
    """One control for one connection. One pending command, never queued, confirmed by the next report."""

    def __init__(self, spec, changed, connection_ready, busy, timeout=10):
        self.spec, self.changed, self.connection_ready, self.busy = spec, changed, connection_ready, busy
        self.timeout = timeout
        self.session = None
        self.guards = {}
        self.raw = None
        self.remote = None
        self.pending = None
        self.uncertain = False
        self.result = 'waiting_for_state'

    def begin(self, session):
        self._interrupt()
        self.session = session; self.guards = {}; self.raw = None; self.remote = None
        self.uncertain = False; self.result = 'waiting_for_state'

    def disconnect(self, session):
        if session != self.session: return
        self._interrupt(); self.session = None; self.guards = {}; self.raw = None
        self.remote = None; self.result = 'disconnected'

    def _interrupt(self):
        if self.pending and not self.pending[2].done():
            self.pending[2].set_exception(ControlRejected('session_changed'))

    def accept(self, session, events):
        if session != self.session or session is None or not isinstance(events, list): return
        for item in events:
            if not isinstance(item, dict) or item.get('component') != 'main': continue
            key, value = (item.get('capability'), item.get('attribute')), item.get('value')
            if key == REMOTE:
                self.remote = True if value in (True, 'true') else False if value in (False, 'false') else None
            if key in GUARD_KEYS:
                if isinstance(value, str) and TOKEN.fullmatch(value): self.guards[key] = value
                else: self.guards.pop(key, None)
            if key == self.spec.state:
                current = self.spec.read(value)
                self.raw = value if current is not None else None
                if current is None: continue
                self.uncertain = False
                if (self.pending and self.pending[0] == session and not self.pending[2].done()
                        and current == self.pending[1]):
                    self.pending[2].set_result(True)

    def _idle(self):
        return (self.spec.guard != 'idle' or (self.guards.get(POWER) == 'on'
                and self.guards.get(OPERATION) == 'ready' and self.guards.get(JOB) == 'none'))

    def snapshot(self):
        value = self.spec.read(self.raw) if self.raw is not None else None
        options = self.spec.options(self.raw) if self.spec.options and self.raw is not None else []
        known = value is not None or self.spec.guard == 'connected'   # these are reported only after a change
        allowed = self.spec.allowed is None or value in self.spec.allowed
        remote = not self.spec.needs_remote or self.remote is True
        ready = (self.session is not None and known and allowed and remote and self.pending is None
                 and not self.uncertain and not self.busy() and self._idle()
                 and self.connection_ready(self.session))
        return {'key': self.spec.key, 'available': bool(ready), 'value': value, 'options': list(options),
                'result': 'ready' if ready and self.result == 'waiting_for_state' else self.result,
                'session': self.session}

    async def set(self, value, send):
        if self.pending is not None: return
        if self.busy():
            self.result = 'busy'; self.changed(); return
        snapshot = self.snapshot()
        if not snapshot['available']:
            self.result = 'not_ready'; self.changed(); return
        button = self.spec.platform == 'button'
        valid = (value is True if button else isinstance(value, bool) if self.spec.platform == 'switch'
                 else isinstance(value, str) and value in snapshot['options'])
        if not valid:
            self.result = 'unsupported_value'; self.changed(); return
        if not button and value == snapshot['value']:
            self.result = 'unchanged'; self.changed(); return
        if self.spec.settable is not None:
            allowed = self.spec.settable(self.raw)
            if allowed is None or value not in allowed:
                self.result = 'not_settable'; self.changed(); return
        session = self.session
        future = asyncio.get_running_loop().create_future()
        pending = (session, self.spec.target if button else value, future)
        self.pending = pending; self.result = 'pending'; self.changed()
        capability, command, arguments = self.spec.command(value)
        try:
            await send(session, capability, command, arguments)
            await asyncio.wait_for(future, timeout=self.timeout)
            self.result = 'confirmed'
        except TimeoutError:
            self.result = 'timeout_unknown'; self.uncertain = True
        except ControlRejected as error:
            self.result = str(error)
        except asyncio.CancelledError:
            self.result = 'interrupted'; raise
        except (OSError, ValueError):
            self.result = 'send_failed_unknown'; self.uncertain = True
        finally:
            if not future.done(): future.cancel()
            elif not future.cancelled(): future.exception()
            if self.pending is pending: self.pending = None
            self.changed()


def engine_keys(device):
    chosen = device.get('controls', {}).get('dishwasher', [])
    if device.get('profile') != 'dishwasher' or not isinstance(chosen, list): return []
    return [key for key in KEYS if key in chosen]


def engine_enabled(device):
    return bool(engine_keys(device))


class DishwasherEngine:
    """The enabled controls of one dishwasher. Only one command may be pending across all of them."""

    def __init__(self, device, changed, connection_ready, timeout=10):
        enabled = engine_keys(device)
        self.controls = {item.key: EngineControl(item, changed, connection_ready, self.pending, timeout)
                         for item in CONTROLS if item.key in enabled}

    def begin(self, session):
        for control in self.controls.values(): control.begin(session)

    def disconnect(self, session):
        for control in self.controls.values(): control.disconnect(session)

    def accept(self, session, events):
        for control in self.controls.values(): control.accept(session, events)

    def pending(self):
        return any(control.pending is not None for control in self.controls.values())

    def snapshots(self):
        return {key: control.snapshot() for key, control in self.controls.items()}
