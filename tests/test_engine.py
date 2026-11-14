import asyncio
import unittest
from unittest.mock import AsyncMock

from fixtures import configuration, event
from thingslocal.engine import CONTROLS, KEYS, DishwasherEngine, EngineControl, engine_enabled, engine_keys

OPTIONS = 'samsungce.dishwasherWashingOptions'
DOOR = 'samsungce.autoDoorRelease'
OPERATION = ('samsungce.dishwasherOperation', 'operatingState')
JOB = ('samsungce.dishwasherJobState', 'dishwasherJobState')
REMOTE = ('remoteControlStatus', 'remoteControlEnabled')


def spec(key):
    return next(item for item in CONTROLS if item.key == key)


class SpecTableTests(unittest.TestCase):
    def test_keys_and_order_are_exactly_the_eight_proven_controls(self):
        self.assertEqual(KEYS, ('sanitize', 'speed_booster', 'selected_zone', 'door_release', 'mute',
                                'pause', 'resume', 'cancel'))
        self.assertEqual([item.platform for item in CONTROLS],
                         ['switch', 'switch', 'select', 'switch', 'switch', 'button', 'button', 'button'])
        self.assertEqual([item.guard for item in CONTROLS], ['idle', 'idle', 'idle'] + ['connected'] * 5)
        self.assertEqual(spec('sanitize').state, (OPTIONS, 'sanitize'))
        self.assertEqual(spec('speed_booster').state, (OPTIONS, 'speedBooster'))
        self.assertEqual(spec('selected_zone').state, (OPTIONS, 'selectedZone'))
        self.assertEqual(spec('door_release').state, ('samsungce.autoDoorRelease', 'autoDoorReleaseEnabled'))
        self.assertEqual(spec('mute').state, ('audioMute', 'mute'))

    def test_read_returns_the_value_for_good_reports_and_none_for_bad_ones(self):
        self.assertIs(spec('sanitize').read({'value': True, 'settable': [True, False]}), True)
        self.assertIs(spec('speed_booster').read({'value': False, 'settable': [False]}), False)
        self.assertEqual(spec('selected_zone').read({'value': 'lower', 'settable': ['all', 'lower']}), 'lower')
        self.assertIs(spec('door_release').read(True), True)
        self.assertIs(spec('mute').read('muted'), True)
        self.assertIs(spec('mute').read('unmuted'), False)
        for key in ('sanitize', 'speed_booster', 'selected_zone'):
            for bad in ({}, {'value': 1}, {'value': None}, None, [], 'on'):
                self.assertIsNone(spec(key).read(bad), (key, bad))
        self.assertIsNone(spec('selected_zone').read({'value': 5}))
        self.assertIsNone(spec('door_release').read('yes'))
        self.assertIsNone(spec('mute').read('loud'))

    def test_settable_lists_come_from_the_report(self):
        self.assertEqual(spec('sanitize').settable({'value': False, 'settable': [False]}), [False])
        self.assertEqual(spec('speed_booster').settable({'value': False, 'settable': [True, False]}), [True, False])
        self.assertEqual(spec('selected_zone').options({'value': 'all', 'settable': ['all', 'lower']}), ['all', 'lower'])
        for bad in ({'value': True}, {'value': True, 'settable': 'yes'}, {'settable': [1]}, None):
            self.assertIsNone(spec('sanitize').settable(bad))
        self.assertEqual(spec('selected_zone').options({'value': 'all'}), [])
        self.assertIsNone(spec('door_release').settable)
        self.assertIsNone(spec('mute').settable)

    def test_each_command_is_the_exact_proven_shape(self):
        self.assertEqual(spec('sanitize').command(True), (OPTIONS, 'setOptions', [{'sanitize': True}]))
        self.assertEqual(spec('sanitize').command(False), (OPTIONS, 'setOptions', [{'sanitize': False}]))
        self.assertEqual(spec('speed_booster').command(True), (OPTIONS, 'setOptions', [{'speedBooster': True}]))
        self.assertEqual(spec('selected_zone').command('lower'), (OPTIONS, 'setOptions', [{'selectedZone': 'lower'}]))
        self.assertEqual(spec('door_release').command(True), ('samsungce.autoDoorRelease', 'enable', []))
        self.assertEqual(spec('door_release').command(False), ('samsungce.autoDoorRelease', 'disable', []))
        self.assertEqual(spec('mute').command(True), ('audioMute', 'mute', []))
        self.assertEqual(spec('mute').command(False), ('audioMute', 'unmute', []))

    def test_engine_keys_only_for_dishwashers_and_only_known_keys_in_order(self):
        config = configuration(); dishwasher, cooktop = config['devices'][1], config['devices'][0]
        self.assertEqual(engine_keys(dishwasher), []); self.assertFalse(engine_enabled(dishwasher))
        dishwasher['controls'] = {'dishwasher': ['mute', 'sanitize', 'nonsense']}
        self.assertEqual(engine_keys(dishwasher), ['sanitize', 'mute']); self.assertTrue(engine_enabled(dishwasher))
        cooktop['controls'] = {'dishwasher': ['mute']}
        self.assertEqual(engine_keys(cooktop), []); self.assertFalse(engine_enabled(cooktop))
        dishwasher['controls'] = {'dishwasher_start': True}
        self.assertEqual(engine_keys(dishwasher), [])


def sanitize_report(value, settable=(True, False)):
    return event(OPTIONS, 'sanitize', {'value': value, 'settable': list(settable)})


def make(key, connected=True, busy=False, timeout=10):
    return EngineControl(spec(key), lambda: None, lambda session: connected, lambda: busy, timeout=timeout)


def ready(control, session=1, state=None):
    control.begin(session)
    control.accept(session, [event('switch', 'switch', 'on'), event(*OPERATION, 'ready'), event(*JOB, 'none')]
                   + ([state] if state is not None else [sanitize_report(False)]))


class EngineControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_a_fresh_matching_report_from_the_bound_session_confirms(self):
        control = make('sanitize'); ready(control); send = AsyncMock()
        task = asyncio.create_task(control.set(True, send)); await asyncio.sleep(0)
        self.assertFalse(control.snapshot()['available']); self.assertIs(control.snapshot()['value'], False)
        control.accept(0, [sanitize_report(True)]); control.accept(1, [sanitize_report(False)])
        await asyncio.sleep(0); self.assertFalse(task.done())
        control.accept(1, [sanitize_report(True)]); await task
        self.assertEqual(control.result, 'confirmed')
        self.assertTrue(control.snapshot()['available']); self.assertIs(control.snapshot()['value'], True)
        send.assert_awaited_once_with(1, OPTIONS, 'setOptions', [{'sanitize': True}])

    async def test_guard_idle_blocks_when_off_busy_or_a_job_runs_and_connected_guard_does_not(self):
        for key, attr, value in (('switch', 'switch', 'off'), (*OPERATION, 'running'), (*JOB, 'washing')):
            control = make('sanitize'); ready(control); send = AsyncMock()
            control.accept(1, [event(key, attr, value)])
            await control.set(True, send); send.assert_not_awaited(); self.assertEqual(control.result, 'not_ready')
        control = make('door_release', timeout=0.001); control.begin(1); send = AsyncMock()
        control.accept(1, [event(DOOR, 'autoDoorReleaseEnabled', True), event('switch', 'switch', 'off'),
                           event(*OPERATION, 'running')])
        await control.set(False, send)
        send.assert_awaited_once_with(1, DOOR, 'disable', [])

    async def test_unchanged_unsupported_and_not_settable_do_not_write(self):
        control = make('sanitize'); ready(control); send = AsyncMock()
        await control.set(False, send); self.assertEqual(control.result, 'unchanged')
        for bad in ('yes', None, 1):
            await control.set(bad, send); self.assertEqual(control.result, 'unsupported_value')
        zone = make('selected_zone'); ready(zone, state=event(OPTIONS, 'selectedZone', {'value': 'all', 'settable': ['all', 'lower']}))
        self.assertEqual(zone.snapshot()['options'], ['all', 'lower'])
        await zone.set('middle', send); self.assertEqual(zone.result, 'unsupported_value')
        await zone.set('all', send); self.assertEqual(zone.result, 'unchanged')
        limited = make('sanitize'); ready(limited, state=sanitize_report(False, settable=(False,)))
        await limited.set(True, send); self.assertEqual(limited.result, 'not_settable')
        send.assert_not_awaited()

    async def test_a_pending_command_blocks_every_control_of_the_device_and_is_never_queued(self):
        control = make('sanitize'); ready(control); send = AsyncMock()
        task = asyncio.create_task(control.set(True, send)); await asyncio.sleep(0)
        await control.set(False, send); send.assert_awaited_once(); self.assertEqual(control.result, 'pending')
        control.accept(1, [sanitize_report(True)]); await task
        other = make('sanitize', busy=True); ready(other); send = AsyncMock()
        await other.set(True, send); send.assert_not_awaited(); self.assertEqual(other.result, 'busy')

    async def test_timeout_is_unknown_and_blocks_until_a_fresh_report_of_the_same_attribute(self):
        control = make('sanitize', timeout=0.001); ready(control); send = AsyncMock()
        await control.set(True, send)
        self.assertEqual(control.result, 'timeout_unknown'); self.assertFalse(control.snapshot()['available'])
        await control.set(True, send); send.assert_awaited_once()
        control.accept(1, [sanitize_report(False)]); self.assertTrue(control.snapshot()['available'])

    async def test_reconnect_interrupts_pending_and_discards_old_state(self):
        control = make('sanitize'); ready(control); send = AsyncMock()
        task = asyncio.create_task(control.set(True, send)); await asyncio.sleep(0)
        control.begin(2); await task
        self.assertEqual(control.result, 'session_changed')
        control.accept(1, [sanitize_report(True)])
        self.assertIsNone(control.snapshot()['value']); self.assertFalse(control.snapshot()['available'])
        send.assert_awaited_once()

    async def test_missing_or_malformed_report_or_no_subscription_makes_it_unavailable(self):
        control = make('sanitize'); ready(control); self.assertTrue(control.snapshot()['available'])
        control.accept(1, [event(OPTIONS, 'sanitize', {'value': 5})]); self.assertFalse(control.snapshot()['available'])
        missing = make('sanitize'); missing.begin(1); self.assertFalse(missing.snapshot()['available'])
        offline = make('sanitize', connected=False); ready(offline); self.assertFalse(offline.snapshot()['available'])
        control.disconnect(1); self.assertEqual(control.result, 'disconnected')

    async def test_a_connected_control_can_be_set_before_its_first_report(self):
        # Found on the real dishwasher: door release and mute are only reported after a change.
        control = make('mute', timeout=1); control.begin(1); send = AsyncMock()
        self.assertTrue(control.snapshot()['available']); self.assertIsNone(control.snapshot()['value'])
        task = asyncio.create_task(control.set(True, send)); await asyncio.sleep(0)
        send.assert_awaited_once_with(1, 'audioMute', 'mute', [])
        control.accept(1, [event('audioMute', 'mute', 'muted')]); await task
        self.assertEqual(control.result, 'confirmed'); self.assertIs(control.snapshot()['value'], True)

    async def test_a_failed_send_is_unknown_and_never_retried(self):
        control = make('sanitize'); ready(control); send = AsyncMock(side_effect=OSError)
        await control.set(True, send)
        self.assertEqual(control.result, 'send_failed_unknown'); send.assert_awaited_once()
        await control.set(True, send); send.assert_awaited_once()


def button(key, state='running', remote=True, **kwargs):
    control = make(key, **kwargs); control.begin(1)
    events = [event(*OPERATION, state)] + ([event(*REMOTE, remote)] if remote is not None else [])
    control.accept(1, events)
    return control


class ButtonTests(unittest.IsolatedAsyncioTestCase):
    def test_button_rows_have_the_exact_proven_commands_and_targets(self):
        pause, resume, cancel = spec('pause'), spec('resume'), spec('cancel')
        for item in (pause, resume, cancel):
            self.assertEqual((item.platform, item.state, item.needs_remote), ('button', OPERATION, True))
        self.assertEqual(pause.command(True), ('dishwasherOperatingState', 'setMachineState', ['pause']))
        self.assertEqual(resume.command(True), ('dishwasherOperatingState', 'setMachineState', ['run']))
        self.assertEqual(cancel.command(True), ('samsungce.dishwasherOperation', 'cancel', []))
        self.assertEqual((pause.allowed, pause.target), (('running',), 'paused'))
        self.assertEqual((resume.allowed, resume.target), (('paused',), 'running'))
        self.assertEqual((cancel.allowed, cancel.target), (('running', 'paused'), 'ready'))

    def test_a_button_is_available_only_in_its_allowed_operating_states(self):
        table = {'pause': {'running'}, 'resume': {'paused'}, 'cancel': {'running', 'paused'}}
        for key, allowed in table.items():
            for state in ('ready', 'running', 'paused'):
                self.assertEqual(button(key, state).snapshot()['available'], state in allowed, (key, state))
        unknown = make('pause'); unknown.begin(1); unknown.accept(1, [event(*REMOTE, True)])
        self.assertFalse(unknown.snapshot()['available'])

    def test_a_button_that_needs_remote_control_is_unavailable_until_it_is_reported_on(self):
        for remote in (None, False, 'false', 'maybe'):
            self.assertFalse(button('pause', remote=remote).snapshot()['available'], remote)
        for remote in (True, 'true'):
            self.assertTrue(button('pause', remote=remote).snapshot()['available'], remote)

    def test_it_becomes_unavailable_again_when_remote_control_is_reported_off(self):
        control = button('pause'); self.assertTrue(control.snapshot()['available'])
        control.accept(1, [event(*REMOTE, False)]); self.assertFalse(control.snapshot()['available'])
        control.accept(1, [event(*REMOTE, 'true')]); self.assertTrue(control.snapshot()['available'])
        control.begin(2); self.assertFalse(control.snapshot()['available'])

    async def test_a_press_is_confirmed_by_the_target_operating_state(self):
        control = button('pause'); send = AsyncMock()
        task = asyncio.create_task(control.set(True, send)); await asyncio.sleep(0)
        send.assert_awaited_once_with(1, 'dishwasherOperatingState', 'setMachineState', ['pause'])
        control.accept(1, [event(*OPERATION, 'running')]); await asyncio.sleep(0); self.assertFalse(task.done())
        control.accept(1, [event(*OPERATION, 'paused')]); await task
        self.assertEqual(control.result, 'confirmed')

    async def test_a_press_in_the_wrong_state_or_with_a_non_true_value_sends_nothing(self):
        send = AsyncMock()
        wrong = button('resume', state='running'); await wrong.set(True, send)
        self.assertEqual(wrong.result, 'not_ready')
        for bad in (False, 'PRESS', 1, None):
            control = button('pause'); await control.set(bad, send)
            self.assertEqual(control.result, 'unsupported_value', bad)
        send.assert_not_awaited()

    async def test_a_press_while_another_command_is_pending_is_refused_and_a_timeout_is_never_repeated(self):
        send = AsyncMock()
        busy = button('pause', busy=True); await busy.set(True, send); self.assertEqual(busy.result, 'busy')
        control = button('cancel', timeout=0.001)
        await control.set(True, send); self.assertEqual(control.result, 'timeout_unknown')
        await control.set(True, send); self.assertEqual(control.result, 'not_ready')
        send.assert_awaited_once()


class DishwasherEngineTests(unittest.IsolatedAsyncioTestCase):
    def device(self, keys, index=1):
        device = configuration()['devices'][index]; device['controls'] = {'dishwasher': keys}
        return device

    def engine(self, keys, index=1):
        return DishwasherEngine(self.device(keys, index), lambda: None, lambda session: True)

    def test_only_configured_controls_exist_and_the_cooktop_has_none(self):
        self.assertEqual(list(self.engine(['mute', 'sanitize']).controls), ['sanitize', 'mute'])
        self.assertEqual(self.engine(['mute'], index=0).controls, {})
        self.assertEqual(self.engine([]).controls, {})

    def test_events_fan_out_and_snapshots_cover_every_control(self):
        engine = self.engine(['sanitize', 'mute']); engine.begin(1)
        engine.accept(1, [event('switch', 'switch', 'on'), event(*OPERATION, 'ready'), event(*JOB, 'none'),
                          sanitize_report(False), event('audioMute', 'mute', 'unmuted')])
        snapshots = engine.snapshots()
        self.assertEqual(list(snapshots), ['sanitize', 'mute'])
        self.assertTrue(snapshots['sanitize']['available']); self.assertIs(snapshots['mute']['value'], False)
        engine.disconnect(1)
        self.assertEqual({item['result'] for item in engine.snapshots().values()}, {'disconnected'})

    async def test_one_pending_command_makes_the_others_busy(self):
        engine = self.engine(['sanitize', 'mute']); engine.begin(1); send = AsyncMock()
        engine.accept(1, [event('switch', 'switch', 'on'), event(*OPERATION, 'ready'), event(*JOB, 'none'),
                          sanitize_report(False), event('audioMute', 'mute', 'unmuted')])
        task = asyncio.create_task(engine.controls['sanitize'].set(True, send)); await asyncio.sleep(0)
        self.assertTrue(engine.pending()); self.assertFalse(engine.snapshots()['mute']['available'])
        await engine.controls['mute'].set(True, send)
        self.assertEqual(engine.controls['mute'].result, 'busy'); send.assert_awaited_once()
        engine.accept(1, [sanitize_report(True)]); await task
        self.assertFalse(engine.pending()); self.assertTrue(engine.snapshots()['mute']['available'])


if __name__ == '__main__':
    unittest.main()
