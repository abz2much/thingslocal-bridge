import json
from types import SimpleNamespace
import unittest

from fixtures import configuration
from thingslocal.bridge import MQTTBridge
from thingslocal.settings import device_identifier


def snapshot(key, value, options=(), available=True, result='ready'):
    return {'key': key, 'available': available, 'value': value, 'options': list(options),
            'result': result, 'session': 5}


def snapshots(**over):
    base = {'sanitize': snapshot('sanitize', False), 'selected_zone': snapshot('selected_zone', 'all', ['all', 'lower']),
            'mute': snapshot('mute', False)}
    base.update(over)
    return base


def bridge(keys=('sanitize', 'selected_zone', 'mute'), index=1):
    config = configuration(); device = config['devices'][index]
    if keys is not None: device['controls'] = {'dishwasher': list(keys)}
    made = MQTTBridge(config, device, 'expiry')
    made._accept_commands = True
    return made, config, device


def flush(made):
    messages = []
    client = SimpleNamespace(publish=lambda topic, payload, **kw: messages.append((topic, payload, kw)),
                             subscribe=lambda *args, **kwargs: None)
    made._flush(client, -1)
    return messages


def button_messages(messages):
    return [(topic, payload) for topic, payload, _ in messages
            if topic.startswith('homeassistant/button/') and not topic.endswith('/start/config')]


def engine_discovery(messages):
    return [(topic, payload) for topic, payload, _ in messages
            if (topic.startswith(('homeassistant/switch/', 'homeassistant/select/'))
                and not topic.endswith('/washing_program/config'))
            or topic.endswith('/control_result/config')]


class EngineBridgeTests(unittest.TestCase):
    def test_nothing_is_published_or_subscribed_when_no_engine_controls_are_enabled(self):
        made, _, _ = bridge(keys=())
        made.update_engine({})
        self.assertTrue(all(payload == '' for _, payload in engine_discovery(flush(made))))
        subscribed = []
        made._on_connect(SimpleNamespace(subscribe=lambda topic, qos=0: subscribed.append(topic)), None, None, 0, None)
        self.assertFalse([topic for topic in subscribed if topic.endswith('/set')])

    def test_switch_and_select_discovery_use_plain_topics_and_are_non_optimistic(self):
        made, config, device = bridge(); made.update_engine(snapshots())
        found = {topic: json.loads(payload) for topic, payload in engine_discovery(flush(made)) if payload}
        identifier = device_identifier(config, device)
        sanitize = found['homeassistant/switch/%s/sanitize/config' % identifier]
        self.assertEqual(sanitize['name'], 'Sanitize')
        self.assertEqual(sanitize['command_topic'], made.engine_topic('sanitize'))
        self.assertEqual(made.engine_topic('sanitize'), 'thingslocal/washer/sanitize/set')
        self.assertEqual(sanitize['state_topic'], 'thingslocal/washer/sanitize')
        self.assertEqual((sanitize['payload_on'], sanitize['payload_off']), ('ON', 'OFF'))
        self.assertEqual((sanitize['optimistic'], sanitize['retain'], sanitize['qos']), (False, False, 0))
        self.assertIn('thingslocal/washer/sanitize/available', [a['topic'] for a in sanitize['availability']])
        self.assertIn(made.base + '/device_availability', [a['topic'] for a in sanitize['availability']])
        self.assertEqual(sanitize['availability_mode'], 'all')
        self.assertEqual(len([t for t in found if t.startswith('homeassistant/switch/')]), 2)
        self.assertEqual(len([t for t in found if t.startswith('homeassistant/select/')]), 1)

    def test_select_options_come_from_the_device_report(self):
        made, config, device = bridge(); made.update_engine(snapshots())
        found = {topic: json.loads(payload) for topic, payload in engine_discovery(flush(made)) if payload}
        zone = found['homeassistant/select/%s/selected_zone/config' % device_identifier(config, device)]
        self.assertEqual(zone['options'], ['all', 'lower'])
        made, _, _ = bridge(); made.update_engine(snapshots(selected_zone=snapshot('selected_zone', None, [])))
        self.assertFalse([t for t, p in engine_discovery(flush(made)) if t.startswith('homeassistant/select/') and p])

    def test_state_and_availability_are_published_retained_and_follow_the_snapshot(self):
        made, _, _ = bridge(); made.update_engine(snapshots(mute=snapshot('mute', True, available=False)))
        sent = {topic: (payload, kw) for topic, payload, kw in flush(made)}
        self.assertEqual(sent['thingslocal/washer/sanitize'][0], 'OFF')
        self.assertEqual(sent['thingslocal/washer/mute'][0], 'ON')
        self.assertEqual(sent['thingslocal/washer/selected_zone'][0], 'all')
        self.assertEqual(sent['thingslocal/washer/sanitize/available'][0], 'online')
        self.assertEqual(sent['thingslocal/washer/mute/available'][0], 'offline')
        self.assertTrue(sent['thingslocal/washer/sanitize'][1]['retain'])
        self.assertTrue(sent['thingslocal/washer/sanitize/available'][1]['retain'])

    def test_a_switch_with_no_report_yet_is_available_but_has_no_state(self):
        made, _, _ = bridge(); made.update_engine(snapshots(mute=snapshot('mute', None)))
        sent = {topic: payload for topic, payload, _ in flush(made)}
        self.assertEqual(sent['thingslocal/washer/mute/available'], 'online')
        self.assertNotIn('thingslocal/washer/mute', sent)

    def message(self, made, topic='thingslocal/washer/sanitize/set', payload=b'ON', retain=False, qos=0):
        made._on_message(None, None, SimpleNamespace(topic=topic, payload=payload, retain=retain, qos=qos))

    def test_bad_set_messages_never_dispatch(self):
        made, _, _ = bridge(keys=('sanitize', 'selected_zone')); made.update_engine(snapshots())
        calls = []; made.set_engine_handler(lambda *args: calls.append(args))
        self.message(made, retain=True); self.message(made, qos=1); self.message(made, payload=b'x' * 65)
        self.message(made, payload=b'\xff'); self.message(made, payload=b'on'); self.message(made, payload=b'true')
        self.message(made, topic='thingslocal/washer/mute/set')
        self.message(made, topic='thingslocal/washer/selected_zone/set', payload=b'middle')
        self.message(made, topic='thingslocal/washer/nonsense/set')
        self.assertEqual(calls, [])

    def test_a_valid_set_dispatches_once_until_engine_done(self):
        made, _, _ = bridge(); made.update_engine(snapshots())
        calls = []; made.set_engine_handler(lambda *args: calls.append(args))
        self.message(made); self.message(made)
        self.assertEqual(calls, [('sanitize', True, 0, 5)])
        made.engine_done(); self.message(made, payload=b'OFF')
        self.assertEqual(calls[-1], ('sanitize', False, 0, 5)); self.assertEqual(len(calls), 2)
        made.engine_done(); self.message(made, topic='thingslocal/washer/selected_zone/set', payload=b'lower')
        self.assertEqual(calls[-1], ('selected_zone', 'lower', 0, 5))
        made.engine_done(); made._on_disconnect(None, None, None, None, None)
        self.message(made); self.assertEqual(len(calls), 3)

    def test_a_cooktop_never_gets_engine_entities_even_if_forced(self):
        made, _, device = bridge(keys=None, index=0)
        device['controls'] = {'dishwasher': ['mute']}
        made = MQTTBridge(configuration(), device, 'expiry'); made._accept_commands = True
        made.update_engine(snapshots())
        self.assertEqual(engine_discovery(flush(made)), [])
        calls = []; made.set_engine_handler(lambda *args: calls.append(args))
        self.message(made, topic=made.plain + '/mute/set'); self.assertEqual(calls, [])

    def test_disabling_a_control_clears_its_retained_discovery(self):
        made, config, device = bridge(keys=('sanitize',)); made.update_engine(snapshots())
        found = dict(engine_discovery(flush(made))); identifier = device_identifier(config, device)
        self.assertTrue(found['homeassistant/switch/%s/sanitize/config' % identifier])
        self.assertEqual(found['homeassistant/switch/%s/mute/config' % identifier], '')
        self.assertEqual(found['homeassistant/select/%s/selected_zone/config' % identifier], '')

    def test_last_result_sensor_is_published(self):
        made, config, device = bridge()
        made.update_engine(snapshots(sanitize=snapshot('sanitize', False, result='not_settable')))
        messages = flush(made); sent = {topic: payload for topic, payload, _ in messages}
        self.assertEqual(sent['thingslocal/washer/last_result'], 'sanitize: not_settable')
        sensor = json.loads(sent['homeassistant/sensor/%s/control_result/config' % device_identifier(config, device)])
        self.assertEqual(sensor['state_topic'], 'thingslocal/washer/last_result')
        self.assertEqual(sensor['entity_category'], 'diagnostic')

    def test_button_discovery_uses_payload_press_and_has_no_state_topic(self):
        made, config, device = bridge(keys=('pause', 'cancel'))
        made.update_engine({'pause': snapshot('pause', 'running'), 'cancel': snapshot('cancel', 'running')})
        messages = button_messages(flush(made)); identifier = device_identifier(config, device)
        found = {topic: json.loads(payload) for topic, payload in messages if payload}
        self.assertEqual(len(found), 2)
        pause = found['homeassistant/button/%s/pause/config' % identifier]
        self.assertEqual(pause['name'], 'Pause'); self.assertEqual(pause['payload_press'], 'PRESS')
        self.assertNotIn('state_topic', pause); self.assertNotIn('optimistic', pause)
        self.assertEqual(pause['command_topic'], made.engine_topic('pause'))
        self.assertEqual(made.engine_topic('pause'), 'thingslocal/washer/pause/set')
        self.assertEqual((pause['retain'], pause['qos']), (False, 0))
        self.assertIn('thingslocal/washer/pause/available', [a['topic'] for a in pause['availability']])
        self.assertEqual(dict(messages)['homeassistant/button/%s/resume/config' % identifier], '')

    def test_button_availability_follows_the_snapshot_and_no_state_is_published(self):
        made, _, _ = bridge(keys=('pause',))
        made.update_engine({'pause': snapshot('pause', 'running', available=False)})
        sent = {topic: payload for topic, payload, _ in flush(made)}
        self.assertEqual(sent['thingslocal/washer/pause/available'], 'offline')
        self.assertNotIn('thingslocal/washer/pause', sent)
        made.update_engine({'pause': snapshot('pause', 'running')})
        sent = {topic: payload for topic, payload, _ in flush(made)}
        self.assertEqual(sent['thingslocal/washer/pause/available'], 'online')

    def test_only_the_exact_press_payload_dispatches_for_a_button(self):
        made, _, _ = bridge(keys=('pause',)); made.update_engine({'pause': snapshot('pause', 'running')})
        calls = []; made.set_engine_handler(lambda *args: calls.append(args))
        topic = 'thingslocal/washer/pause/set'
        self.message(made, topic=topic, payload=b'PRESS', retain=True); self.message(made, topic=topic, payload=b'PRESS', qos=1)
        for bad in (b'press', b'ON', b'', b'x' * 65, b'\xff'):
            self.message(made, topic=topic, payload=bad)
        self.assertEqual(calls, [])
        self.message(made, topic=topic, payload=b'PRESS'); self.message(made, topic=topic, payload=b'PRESS')
        self.assertEqual(calls, [('pause', True, 0, 5)])
        made.engine_done(); self.message(made, topic=topic, payload=b'PRESS'); self.assertEqual(len(calls), 2)

    def test_a_cooktop_never_gets_a_button(self):
        made, _, device = bridge(keys=None, index=0)
        device['controls'] = {'dishwasher': ['pause']}
        made = MQTTBridge(configuration(), device, 'expiry'); made._accept_commands = True
        made.update_engine({'pause': snapshot('pause', 'running')})
        self.assertEqual([item for item in button_messages(flush(made)) if item[1]], [])
        calls = []; made.set_engine_handler(lambda *args: calls.append(args))
        self.message(made, topic=made.plain + '/pause/set', payload=b'PRESS'); self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
