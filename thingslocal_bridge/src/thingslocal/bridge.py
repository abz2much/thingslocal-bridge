"""Bounded MQTT discovery, telemetry and opt-in dishwasher program selection."""
import json
import logging
import threading
from uuid import UUID
import paho.mqtt.client as mqtt
from .settings import device_identifier
from .controls import course_enabled, start_enabled
from .engine import CONTROLS, engine_keys

SPECS = {item.key: item for item in CONTROLS}


def discovery_messages(identifier, base, device_config, entities):
    device = {'identifiers': [identifier], 'name': device_config['name'],
              'manufacturer': device_config.get('vendor_name', 'SmartThings appliance'),
              'model': device_config['identity']['model']}
    for entity in entities:
        key, platform = entity['key'], entity['platform']
        config = {
            'name': entity['name'], 'unique_id': identifier + '_' + key,
            'default_entity_id': platform + '.' + identifier + '_' + key,
            'device': device, 'state_topic': base + '/state',
            'value_template': '{{ value_json.get(' + repr(key) + ') }}',
            'availability_mode': 'all',
            'availability': [
                {'topic': base + '/bridge_availability'},
                {'topic': base + '/device_availability'},
                {'topic': base + '/state', 'value_template':
                 "{{ 'online' if " + repr(key) + " in value_json else 'offline' }}"},
            ], **entity['options'],
        }
        if entity.get('attributes_key'):
            config['json_attributes_topic'] = base + '/state'
            config['json_attributes_template'] = '{{ value_json.get(' + repr(entity['attributes_key']) + ', {}) | tojson }}'
        yield f'homeassistant/{platform}/{identifier}/{key}/config', config
    yield f'homeassistant/sensor/{identifier}/certificate_expiry/config', {
        'name': 'TLS certificate expiry', 'unique_id': identifier + '_certificate_expiry',
        'default_entity_id': 'sensor.' + identifier + '_certificate_expiry',
        'device': device, 'device_class': 'timestamp', 'entity_category': 'diagnostic',
        'state_topic': base + '/certificate_expiry',
        'availability_topic': base + '/bridge_availability',
    }


class MQTTBridge:
    """One coalesced snapshot and one worker; a fresh Paho client per connection.

    Connection attempts run outside the appliance's asyncio loop. Recreating the
    client discards queued messages from older sessions instead of replaying an
    old retained 'online' after a newer 'offline'.
    """

    def __init__(self, config, device, certificate_expiry):
        self.config = {'mqtt_host': config['mqtt']['host'], 'mqtt_port': config['mqtt']['port'],
                       'mqtt_username': config['mqtt']['username'], 'mqtt_password': config['mqtt']['password']}
        self.identifier = device_identifier(config, device)
        self.base = config['mqtt'].get('namespace', 'thingslocal') + '/' + self.identifier
        self.device = device
        self._entities = []
        self.certificate_expiry = certificate_expiry
        self._lock = threading.Lock()
        self._values = {}
        self._online = False
        self._revision = 0
        self._refresh = True
        self._stop = threading.Event()
        self._thread = None
        self._logger = logging.getLogger('thingslocal.bridge')
        self._course_enabled = course_enabled(device)
        self.command_topic = self.base + '/command/washing_program'
        self._control = {'options': [], 'value': None, 'available': False,
                         'result': 'waiting_for_state', 'session': None}
        self._course_options = []
        self._command_handler = None
        self._command_pending = False
        self._command_epoch = 0
        self._accept_commands = False
        self._notification = None
        self._notification_refresh = True
        self._published_notification_id = None
        self._acknowledgement_handler = None
        self._acknowledgement_pending = False
        self._start_enabled = start_enabled(device)
        self.start_topic = self.base + '/command/start'
        self._start_result = 'waiting_for_state'
        self._start_handler = None
        self._start_pending = False
        self._engine_keys = engine_keys(device)
        self.plain = config['mqtt'].get('namespace', 'thingslocal') + '/' + device['id']
        self._engine = {}
        self._engine_last = None
        self._engine_handler = None
        self._engine_pending = False

    def engine_topic(self, key): return self.plain + '/' + key + '/set'

    def set_engine_handler(self, handler): self._engine_handler = handler

    def engine_done(self):
        with self._lock: self._engine_pending = False

    def reject_engine(self, key, reason):
        with self._lock:
            self._engine_last = key + ': ' + reason; self._revision += 1

    def update_engine(self, snapshots):
        with self._lock:
            for key, current in snapshots.items():
                old = self._engine.get(key, {})
                if current['options'] != old.get('options'): self._refresh = True
                if current['result'] != old.get('result') and current['result'] not in ('ready', 'waiting_for_state'):
                    self._engine_last = key + ': ' + current['result']
            self._engine = {key: dict(value) for key, value in snapshots.items()}
            self._revision += 1

    def _on_engine_message(self, message):
        key = message.topic[len(self.plain) + 1:-len('/set')]
        if key not in self._engine_keys or self._engine_handler is None: return
        if message.retain: self.reject_engine(key, 'retained_replay_ignored'); return
        if message.qos != 0: self.reject_engine(key, 'unsupported_qos'); return
        if len(message.payload) > 64: self.reject_engine(key, 'invalid_payload'); return
        try: text = message.payload.decode('utf-8')
        except UnicodeDecodeError: self.reject_engine(key, 'invalid_payload'); return
        with self._lock:
            options = list(self._engine.get(key, {}).get('options', []))
        platform = SPECS[key].platform
        if platform == 'button':
            if text != 'PRESS': self.reject_engine(key, 'invalid_payload'); return
            value = True
        elif platform == 'switch':
            if text not in ('ON', 'OFF'): self.reject_engine(key, 'invalid_payload'); return
            value = text == 'ON'
        else:
            if text not in options: self.reject_engine(key, 'unsupported_value'); return
            value = text
        with self._lock:
            if self._engine_pending or not self._accept_commands:
                self._engine_last = key + ': not_ready'; self._revision += 1; return
            self._engine_pending = True
            epoch, session = self._command_epoch, self._engine.get(key, {}).get('session')
        try: self._engine_handler(key, value, epoch, session)
        except RuntimeError:
            self.engine_done(); self.reject_engine(key, 'disconnected')

    def _publish_engine_discovery(self, client, device, engine):
        if self.device['profile'] != 'dishwasher': return
        for spec in CONTROLS:
            topic = f'homeassistant/{spec.platform}/{self.identifier}/{spec.key}/config'
            options = engine.get(spec.key, {}).get('options', [])
            if spec.key not in self._engine_keys or (spec.platform == 'select' and not options):
                client.publish(topic, '', qos=1, retain=True); continue
            state = self.plain + '/' + spec.key
            config = {'name': spec.name, 'unique_id': self.identifier + '_' + spec.key,
                      'default_entity_id': spec.platform + '.' + self.identifier + '_' + spec.key,
                      'device': device, 'command_topic': state + '/set',
                      'retain': False, 'qos': 0, 'icon': spec.icon,
                      'availability_mode': 'all',
                      'availability': [{'topic': self.base + '/bridge_availability'},
                                       {'topic': self.base + '/device_availability'},
                                       {'topic': state + '/available'}]}
            if spec.platform == 'button':
                config['payload_press'] = 'PRESS'
            else:
                config.update(state_topic=state, optimistic=False)
            if spec.platform == 'switch':
                config.update(payload_on='ON', payload_off='OFF', state_on='ON', state_off='OFF')
            elif spec.platform == 'select':
                config['options'] = list(options)
            client.publish(topic, json.dumps(config), qos=1, retain=True)
        result_topic = f'homeassistant/sensor/{self.identifier}/control_result/config'
        if self._engine_keys:
            client.publish(result_topic, json.dumps({
                'name': 'Control result', 'unique_id': self.identifier + '_control_result',
                'default_entity_id': 'sensor.' + self.identifier + '_control_result', 'device': device,
                'entity_category': 'diagnostic', 'state_topic': self.plain + '/last_result',
                'availability_topic': self.base + '/bridge_availability'}), qos=1, retain=True)
        else:
            client.publish(result_topic, '', qos=1, retain=True)

    def _publish_engine_state(self, client, engine, last):
        for key in self._engine_keys:
            current = engine.get(key)
            if current is None: continue
            topic, value = self.plain + '/' + key, current['value']
            if SPECS[key].platform == 'switch' and isinstance(value, bool):
                client.publish(topic, 'ON' if value else 'OFF', qos=0, retain=True)
            elif SPECS[key].platform == 'select' and value in current['options']:
                client.publish(topic, value, qos=0, retain=True)
            client.publish(topic + '/available', 'online' if current['available'] else 'offline', qos=0, retain=True)
        if self._engine_keys and last: client.publish(self.plain + '/last_result', last, qos=0, retain=True)

    def set_start_handler(self, handler): self._start_handler = handler

    def start_done(self):
        with self._lock: self._start_pending = False

    def update_start(self, result):
        with self._lock:
            self._start_result = result; self._revision += 1

    def set_acknowledgement_handler(self, handler): self._acknowledgement_handler = handler

    def acknowledgement_done(self):
        with self._lock: self._acknowledgement_pending = False

    def update_notification(self, notification):
        with self._lock:
            self._notification = dict(notification) if notification else None
            self._revision += 1

    def set_command_handler(self, handler): self._command_handler = handler

    def update_control(self, snapshot):
        with self._lock:
            if snapshot['options'] and snapshot['options'] != self._course_options:
                self._course_options = list(snapshot['options']); self._refresh = True
            self._control = dict(snapshot); self._revision += 1

    def command_current(self, epoch):
        with self._lock:
            return self._accept_commands and epoch == self._command_epoch and not self._stop.is_set()

    def command_done(self):
        with self._lock: self._command_pending = False

    def reject_command(self, reason):
        with self._lock:
            self._control['result'] = reason; self._revision += 1

    def update(self, values, online, entities):
        snapshots = [entity.dictionary() for entity in entities.values()]
        with self._lock:
            if snapshots != self._entities:
                self._entities = snapshots
                self._refresh = True
            self._values = dict(values)
            self._online = online
            self._revision += 1

    def start(self):
        self._thread = threading.Thread(target=self._run, name='ha-mqtt-bridge', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        with self._lock:
            self._accept_commands = False; self._command_epoch += 1
        if self._thread is not None:
            self._thread.join(timeout=8)

    def _on_connect(self, client, userdata, flags, reason, properties):
        if reason != 0:
            return
        client.subscribe('homeassistant/status', qos=1)
        if self._course_enabled: client.subscribe(self.command_topic, qos=0)
        if self._start_enabled: client.subscribe(self.start_topic, qos=0)
        for key in self._engine_keys: client.subscribe(self.engine_topic(key), qos=0)
        if self.device['profile'] == 'dishwasher': client.subscribe(self.base + '/notification_ack', qos=1)
        with self._lock:
            self._refresh = True; self._notification_refresh = True
            self._accept_commands = True; self._command_epoch += 1

    def _on_disconnect(self, client, userdata, flags, reason, properties):
        with self._lock:
            self._accept_commands = False; self._command_epoch += 1

    def _on_message(self, client, userdata, message):
        if (message.topic == self.base + '/notification_ack' and self._acknowledgement_handler is not None
                and len(message.payload) <= 36):
            try:
                identifier = message.payload.decode('ascii')
                if str(UUID(identifier)) != identifier: return
            except (ValueError, UnicodeDecodeError): return
            with self._lock:
                if (self._acknowledgement_pending or self._notification is None
                        or self._notification['id'] != identifier): return
                self._acknowledgement_pending = True
            try: self._acknowledgement_handler(identifier)
            except RuntimeError: self.acknowledgement_done()
            return
        if message.topic == 'homeassistant/status' and message.payload == b'online':
            with self._lock:
                self._refresh = True; self._notification_refresh = True
            return
        if message.topic.startswith(self.plain + '/') and message.topic.endswith('/set'):
            self._on_engine_message(message); return
        if message.topic == self.start_topic:
            self._on_start_message(message); return
        if message.topic != self.command_topic or not self._course_enabled or self._command_handler is None: return
        if message.retain:
            self.reject_command('retained_replay_ignored'); return
        if message.qos != 0:
            self.reject_command('unsupported_qos'); return
        if len(message.payload) > 64:
            self.reject_command('invalid_program'); return
        try: course = message.payload.decode('utf-8')
        except UnicodeDecodeError:
            self.reject_command('invalid_program'); return
        with self._lock:
            if self._command_pending or not self._accept_commands or not self._control['available']:
                self._control['result'] = 'not_ready'; self._revision += 1; return
            if course not in self._control['options']:
                self._control['result'] = 'unsupported_program'; self._revision += 1; return
            self._command_pending = True
            epoch, session = self._command_epoch, self._control['session']
        try: self._command_handler(course, epoch, session)
        except RuntimeError:
            self.command_done(); self.reject_command('disconnected')

    def _on_start_message(self, message):
        if not self._start_enabled or self._start_handler is None: return
        if message.retain: self.update_start('retained_replay_ignored'); return
        if message.qos != 0: self.update_start('unsupported_qos'); return
        if message.payload != b'PRESS': self.update_start('invalid_payload'); return
        with self._lock:
            if self._start_pending or not self._accept_commands:
                self._start_result = 'not_ready'; self._revision += 1; return
            self._start_pending = True
            epoch = self._command_epoch
        try: self._start_handler(epoch)
        except RuntimeError:
            self.start_done(); self.update_start('disconnected')

    def _flush(self, client, sent_revision):
        with self._lock:
            values, online = dict(self._values), self._online
            revision, refresh = self._revision, self._refresh
            self._refresh = False
            entities = list(self._entities)
            control, options = dict(self._control), list(self._course_options)
            notification = dict(self._notification) if self._notification else None
            notification_refresh = self._notification_refresh
            self._notification_refresh = False
            start_result = self._start_result
            engine, engine_last = {key: dict(value) for key, value in self._engine.items()}, self._engine_last
        if refresh:
            for topic, config in discovery_messages(self.identifier, self.base, self.device, entities):
                client.publish(topic, json.dumps(config), qos=1, retain=True)
            client.publish(self.base + '/certificate_expiry', self.certificate_expiry,
                           qos=1, retain=True)
            device = {'identifiers': [self.identifier], 'name': self.device['name'],
                      'manufacturer': self.device.get('vendor_name', 'SmartThings appliance'),
                      'model': self.device['identity']['model']}
            select_topic = f'homeassistant/select/{self.identifier}/washing_program/config'
            result_topic = f'homeassistant/sensor/{self.identifier}/program_selection_result/config'
            if self.device['profile'] == 'dishwasher':
                for key, name in (('notification_event', 'Notification event'), ('notification_ack', 'Notification acknowledgement')):
                    discovery = {'name': name, 'unique_id': self.identifier + '_' + key,
                        'default_entity_id': 'sensor.' + self.identifier + '_' + key,
                        'device': device, 'entity_category': 'diagnostic', 'state_topic': self.base + '/' + key}
                    if key == 'notification_event':
                        discovery.update(value_template='{{ value_json.id }}',
                            json_attributes_topic=self.base + '/' + key,
                            json_attributes_template="{{ {'message': value_json.message, 'kind': value_json.kind, 'codes': value_json.codes, 'created': value_json.created} | tojson }}")
                    client.publish(f'homeassistant/sensor/{self.identifier}/{key}/config', json.dumps(discovery), qos=1, retain=True)
            if self._course_enabled:
                if options:
                    client.publish(select_topic, json.dumps({
                        'name': 'Washing program', 'unique_id': self.identifier + '_washing_program',
                        'default_entity_id': 'select.' + self.identifier + '_washing_program', 'device': device,
                        'options': options, 'command_topic': self.command_topic,
                        'state_topic': self.base + '/washing_program', 'optimistic': False,
                        'retain': False, 'qos': 0, 'icon': 'mdi:dishwasher', 'availability_mode': 'all',
                        'availability': [{'topic': self.base + '/' + topic} for topic in
                                         ('bridge_availability', 'device_availability', 'program_availability')],
                    }), qos=1, retain=True)
                client.publish(result_topic, json.dumps({
                    'name': 'Program selection result', 'unique_id': self.identifier + '_program_selection_result',
                    'default_entity_id': 'sensor.' + self.identifier + '_program_selection_result',
                    'device': device, 'entity_category': 'diagnostic',
                    'state_topic': self.base + '/program_selection_result',
                    'availability_topic': self.base + '/bridge_availability',
                }), qos=1, retain=True)
            else:
                client.publish(select_topic, '', qos=1, retain=True)
                client.publish(result_topic, '', qos=1, retain=True)
            button_topic = f'homeassistant/button/{self.identifier}/start/config'
            start_result_topic = f'homeassistant/sensor/{self.identifier}/start_result/config'
            if self._start_enabled:
                client.publish(button_topic, json.dumps({
                    'name': 'Start', 'unique_id': self.identifier + '_start',
                    'default_entity_id': 'button.' + self.identifier + '_start', 'device': device,
                    'command_topic': self.start_topic, 'payload_press': 'PRESS',
                    'retain': False, 'qos': 0, 'icon': 'mdi:play', 'availability_mode': 'all',
                    'availability': [{'topic': self.base + '/' + topic} for topic in
                                     ('bridge_availability', 'device_availability')],
                }), qos=1, retain=True)
                client.publish(start_result_topic, json.dumps({
                    'name': 'Start result', 'unique_id': self.identifier + '_start_result',
                    'default_entity_id': 'sensor.' + self.identifier + '_start_result',
                    'device': device, 'entity_category': 'diagnostic',
                    'state_topic': self.base + '/start_result',
                    'availability_topic': self.base + '/bridge_availability',
                }), qos=1, retain=True)
            else:
                client.publish(button_topic, '', qos=1, retain=True)
                client.publish(start_result_topic, '', qos=1, retain=True)
            self._publish_engine_discovery(client, device, engine)
        if refresh or revision != sent_revision:
            # State first: a new session clears per-field availability before
            # advertising it as connected. Missing values never become zero.
            client.publish(self.base + '/state', json.dumps(values), qos=0, retain=True)
            client.publish(self.base + '/device_availability',
                           'online' if online else 'offline', qos=0, retain=True)
            if self._course_enabled:
                if control['value'] in options:
                    client.publish(self.base + '/washing_program', control['value'], qos=0, retain=True)
                client.publish(self.base + '/program_selection_result', control['result'], qos=0, retain=True)
            if self._start_enabled:
                client.publish(self.base + '/start_result', start_result, qos=0, retain=True)
            self._publish_engine_state(client, engine, engine_last)
            client.publish(self.base + '/program_availability',
                           'online' if self._course_enabled and control['available'] else 'offline', qos=0, retain=True)
            if self.device['profile'] == 'dishwasher':
                value = notification or {'id': 'none', 'message': '', 'kind': 'none', 'codes': [], 'created': ''}
                if notification_refresh or value['id'] != self._published_notification_id:
                    client.publish(self.base + '/notification_event', json.dumps(value), qos=1, retain=True)
                    self._published_notification_id = value['id']
        if refresh:
            client.publish(self.base + '/bridge_availability', 'online', qos=0, retain=True)
        return revision

    def _run(self):
        delay = 1
        while not self._stop.is_set():
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                 client_id=self.identifier + '_bridge', clean_session=True)
            client.connect_timeout = 5
            client.max_queued_messages_set(256)
            client.max_inflight_messages_set(16)
            client.username_pw_set(self.config['mqtt_username'], self.config['mqtt_password'])
            client.will_set(self.base + '/bridge_availability', 'offline', qos=1, retain=True)
            client.on_connect = self._on_connect
            client.on_message = self._on_message
            client.on_disconnect = self._on_disconnect
            try:
                client.connect(self.config['mqtt_host'], self.config['mqtt_port'], keepalive=30)
                sent_revision = -1
                while not self._stop.is_set():
                    if client.loop(timeout=0.5) != mqtt.MQTT_ERR_SUCCESS:
                        break
                    if client.is_connected():
                        delay = 1
                        sent_revision = self._flush(client, sent_revision)
            except (OSError, ValueError):
                self._logger.warning('Home Assistant MQTT connection unavailable; retrying')
            finally:
                with self._lock:
                    self._accept_commands = False; self._command_epoch += 1
                if self._stop.is_set() and client.is_connected():
                    client.publish(self.base + '/bridge_availability', 'offline', qos=0, retain=True)
                    client.loop(timeout=0.2)
                client.disconnect()
            if not self._stop.is_set():
                self._stop.wait(delay)
                delay = min(delay * 2, 30)
