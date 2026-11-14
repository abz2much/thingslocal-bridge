import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from localthings.server import Device, Subscription
from fixtures import configuration, event, registration
from test_server import Connection, status, token
from thingslocal.controls import ControlRejected
from thingslocal.server import ApplianceServer
from thingslocal.bridge import MQTTBridge
from thingslocal.controls import DishwasherStartControl, start_enabled
from thingslocal.settings import runtime_config, validate
from thingslocal.telemetry import DeviceTelemetry


class RemoteControlStatusTests(unittest.TestCase):
    def state(self, index=1):
        state = DeviceTelemetry(configuration()['devices'][index]); state.begin(1)
        return state

    def test_dishwasher_reports_remote_control_as_binary_sensor(self):
        state = self.state()
        for value, expected in (('true', 'ON'), ('false', 'OFF'), (True, 'ON'), (False, 'OFF')):
            state.accept(1, [event('remoteControlStatus', 'remoteControlEnabled', value)])
            entity = next(e for e in state.entities.values() if e.name == 'Remote control enabled')
            self.assertEqual(entity.platform, 'binary_sensor')
            self.assertEqual(state.values[entity.key], expected)
        state.accept(1, [event('remoteControlStatus', 'remoteControlEnabled', 'maybe')])
        self.assertNotIn(entity.key, state.values)


class StartOptInTests(unittest.TestCase):
    def test_off_by_default_dishwasher_only_and_boolean(self):
        config = configuration()
        self.assertFalse(any(start_enabled(d) for d in config['devices']))
        config['devices'][1]['controls'] = {'dishwasher_start': 'true'}
        with self.assertRaises(ValueError): validate(config)
        config = configuration(); config['devices'][0]['controls'] = {'dishwasher_start': True}
        with self.assertRaises(ValueError): validate(config)
        config = configuration(); config['devices'][1]['controls'] = {'dishwasher_start': True}
        exported = runtime_config(validate(config))['devices'][1]
        self.assertTrue(exported['controls']['dishwasher_start'])
        self.assertTrue(start_enabled(exported))
        self.assertFalse(start_enabled(config['devices'][0] | {'controls': {'dishwasher_start': True}}))


def start_device():
    device = configuration()['devices'][1]; device['controls'] = {'dishwasher_start': True}
    return device


REMOTE = ('remoteControlStatus', 'remoteControlEnabled')


class StartGuardTests(unittest.IsolatedAsyncioTestCase):
    def control(self, connected=True, device=None):
        return DishwasherStartControl(device or start_device(), lambda: None, lambda session: connected)

    async def test_sends_once_when_remote_control_is_enabled(self):
        for value in ('true', True):
            control = self.control(); control.begin(7); send = AsyncMock()
            control.accept(7, [event(*REMOTE, value)])
            await control.start(send)
            send.assert_awaited_once_with(7); self.assertEqual(control.result, 'sent_unconfirmed')

    async def test_nothing_is_sent_when_guard_fails(self):
        cases = {'remote_control_disabled': [event(*REMOTE, 'false')],
                 'remote_control_unknown': [], 'remote_control_unknown2': [event(*REMOTE, 'bogus')]}
        for name, events in cases.items():
            control = self.control(); control.begin(7); control.accept(7, events); send = AsyncMock()
            await control.start(send); send.assert_not_awaited()
            self.assertEqual(control.result, name.rstrip('2'))
        control = self.control(connected=False); control.begin(7)
        control.accept(7, [event(*REMOTE, 'true')]); send = AsyncMock()
        await control.start(send); send.assert_not_awaited(); self.assertEqual(control.result, 'not_connected')
        control = self.control(); send = AsyncMock()
        await control.start(send); send.assert_not_awaited(); self.assertEqual(control.result, 'not_connected')
        control = self.control(device=configuration()['devices'][1]); control.begin(7)
        control.accept(7, [event(*REMOTE, 'true')]); send = AsyncMock()
        await control.start(send); send.assert_not_awaited(); self.assertEqual(control.result, 'control_disabled')

    async def test_stale_status_is_dropped_on_reconnect_and_disconnect(self):
        control = self.control(); control.begin(7); control.accept(7, [event(*REMOTE, 'true')])
        control.begin(8); send = AsyncMock(); await control.start(send); send.assert_not_awaited()
        control.accept(7, [event(*REMOTE, 'true')]); await control.start(send); send.assert_not_awaited()
        control.accept(8, [event(*REMOTE, 'true')]); control.disconnect(8)
        await control.start(send); send.assert_not_awaited()

    async def test_repeat_press_is_blocked_until_a_fresh_operating_state_report(self):
        control = self.control(); control.begin(7); control.accept(7, [event(*REMOTE, 'true')])
        send = AsyncMock(); await control.start(send); await control.start(send)
        send.assert_awaited_once(); self.assertEqual(control.result, 'already_sent')
        control.accept(7, [event('samsungce.dishwasherOperation', 'operatingState', 'running')])
        control.accept(7, [event(*REMOTE, 'true')])
        await control.start(send); self.assertEqual(send.await_count, 2)

    async def test_failed_send_is_reported_and_not_retried(self):
        control = self.control(); control.begin(7); control.accept(7, [event(*REMOTE, 'true')])
        send = AsyncMock(side_effect=OSError); await control.start(send)
        self.assertEqual(control.result, 'send_failed_unknown'); send.assert_awaited_once()
        await control.start(send); send.assert_awaited_once()


class StartCommandShapeTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_command_is_exact_and_addressed_to_the_bound_session(self):
        config = configuration(); selected = config['devices'][1]
        selected['controls'] = {'dishwasher_start': True}
        with tempfile.TemporaryDirectory() as directory:
            server = ApplianceServer(config, directory, None, None, status(config), lambda: None)
            record = registration(selected); server.registerDevice(Device.fromDict(record))
            connection = Connection(selected['ip']); connection._logger = connection._logger.bind(connid=11)
            connection._client = server._clientFactory(connection, record['id'], token(selected))
            connection._closeConnection = False
            connection._subscriptions = {Subscription('/v1/commands/' + record['id']): 1}
            connection.publish = AsyncMock(); server._connections.add(connection)
            await server.start_dishwasher(selected['id'], 11)
            args, kwargs = connection.publish.await_args
            self.assertEqual(str(args[0]), '/v1/commands/' + record['id']); self.assertEqual(kwargs, {'qos': 1})
            commands = args[1]['commands']; self.assertEqual(len(commands), 1)
            command = dict(commands[0]); self.assertEqual(len(command.pop('id')), 36)
            self.assertEqual(command, {'component': 'main', 'capability': 'dishwasherOperatingState',
                                       'command': 'setMachineState', 'arguments': ['run']})
            with self.assertRaises(ControlRejected): await server.start_dishwasher(selected['id'], 10)
            with self.assertRaises(ControlRejected): await server.start_dishwasher(config['devices'][0]['id'], 11)
            connection.publish.assert_awaited_once()

    async def test_start_is_refused_when_only_program_selection_is_enabled(self):
        config = configuration(); selected = config['devices'][1]
        selected['controls'] = {'dishwasher_course': True}
        with tempfile.TemporaryDirectory() as directory:
            server = ApplianceServer(config, directory, None, None, status(config), lambda: None)
            with self.assertRaises(ControlRejected): await server.start_dishwasher(selected['id'], 11)


class StartButtonBridgeTests(unittest.TestCase):
    def bridge(self, enabled=True, index=1):
        config = configuration(); device = config['devices'][index]
        if enabled: device['controls'] = {'dishwasher_start': True}
        return MQTTBridge(config, device, 'expiry')

    def flush(self, bridge):
        messages = []
        client = SimpleNamespace(publish=lambda topic, payload, **kw: messages.append((topic, payload)),
                                 subscribe=lambda *args, **kwargs: None)
        bridge._flush(client, -1); return messages

    def buttons(self, messages):
        return [(t, p) for t, p in messages if t.startswith('homeassistant/button/')]

    def test_button_is_not_published_when_opt_in_is_off(self):
        sent = self.buttons(self.flush(self.bridge(enabled=False)))
        self.assertTrue(all(payload == '' for _, payload in sent))
        self.assertFalse(any(t.endswith('/start_result/config') and p for t, p in self.flush(self.bridge(False))))

    def test_button_is_published_when_opt_in_is_on(self):
        bridge = self.bridge(); sent = [item for item in self.buttons(self.flush(bridge)) if item[1]]
        self.assertEqual(len(sent), 1); config = json.loads(sent[0][1])
        self.assertEqual(config['name'], 'Start'); self.assertEqual(config['command_topic'], bridge.start_topic)
        self.assertEqual((config['qos'], config['retain']), (0, False))
        self.assertEqual(config['payload_press'], 'PRESS')
        self.assertIn(bridge.base + '/device_availability', [a['topic'] for a in config['availability']])

    def test_cooktop_never_gets_a_button_even_if_the_flag_is_forced(self):
        bridge = self.bridge(enabled=False, index=0)
        bridge.device['controls'] = {'dishwasher_start': True}
        bridge = MQTTBridge(configuration(), bridge.device, 'expiry')
        self.assertFalse(any(payload for _, payload in self.buttons(self.flush(bridge))))
        calls = []; bridge.set_start_handler(calls.append)
        bridge._accept_commands = True
        bridge._on_message(None, None, SimpleNamespace(topic=bridge.start_topic, payload=b'PRESS', retain=False, qos=0))
        self.assertEqual(calls, [])

    def test_press_dispatches_once_and_bad_messages_do_not(self):
        bridge = self.bridge(); calls = []; bridge.set_start_handler(lambda epoch: calls.append(epoch))
        bridge._accept_commands = True
        def message(**kwargs):
            values = dict(topic=bridge.start_topic, payload=b'PRESS', retain=False, qos=0)
            values.update(kwargs); bridge._on_message(None, None, SimpleNamespace(**values))
        message(retain=True); message(qos=1); message(payload=b'start'); message(payload=b'PRESS' * 99)
        self.assertEqual(calls, [])
        message(); message(); self.assertEqual(calls, [0])
        bridge.start_done(); message(); self.assertEqual(len(calls), 2)
        bridge._on_disconnect(None, None, None, None, None); message(); self.assertEqual(len(calls), 2)

    def test_result_is_published_and_command_topic_subscribed_only_when_enabled(self):
        bridge = self.bridge(); bridge.update_start('remote_control_disabled')
        messages = self.flush(bridge)
        self.assertIn((bridge.base + '/start_result', 'remote_control_disabled'), messages)
        subscribed = []
        client = SimpleNamespace(subscribe=lambda topic, qos=0: subscribed.append(topic))
        bridge._on_connect(client, None, None, 0, None); self.assertIn(bridge.start_topic, subscribed)
        subscribed.clear(); self.bridge(False)._on_connect(client, None, None, 0, None)
        self.assertNotIn(bridge.start_topic, subscribed)


if __name__ == '__main__':
    unittest.main()
