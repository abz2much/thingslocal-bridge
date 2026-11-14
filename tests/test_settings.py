import copy
import json
from pathlib import Path
import tempfile
import unittest
from fixtures import configuration, registration
from thingslocal.settings import atomic_json, device_identifier, load_registry, runtime_config, validate


class ConfigTests(unittest.TestCase):
    def test_two_different_models_and_networks_are_configuration_not_source(self):
        config = configuration(); self.assertEqual(len(validate(config)['devices']), 2)
        ids = [device_identifier(config, item) for item in config['devices']]
        self.assertNotEqual(*ids)
        config['installation_id'] = '001122334455'
        self.assertNotEqual(ids[0], device_identifier(config, config['devices'][0]))

    def test_duplicate_identity_address_or_label_is_rejected(self):
        for key in ('ip', 'id', 'label'):
            config = configuration(); config['devices'][1][key] = config['devices'][0][key]
            with self.subTest(key=key), self.assertRaises(ValueError): validate(config)
        config = configuration(); config['devices'][1]['identity']['serial'] = config['devices'][0]['identity']['serial']
        with self.assertRaises(ValueError): validate(config)

    def test_public_multicast_and_loopback_appliance_addresses_are_rejected(self):
        for address in ('8.8.8.8', '127.0.0.1', '0.0.0.0', '224.0.0.1', '169.254.1.1'):
            config = configuration(); config['devices'][0]['ip'] = address
            with self.subTest(ip=address), self.assertRaises(ValueError): validate(config)

    def test_setup_id_is_never_implicitly_zero(self):
        config = configuration(); del config['devices'][0]['identity']['setup_id']
        with self.assertRaises(ValueError): validate(config)

    def test_explicit_mapping_cannot_create_controls(self):
        config = configuration(); config['devices'][0]['entities'] = [
            {'platform': 'switch', 'component': 'main', 'capability': 'switch', 'attribute': 'switch'}]
        with self.assertRaises(ValueError): validate(config)

    def test_dishwasher_control_list_accepts_known_unique_keys_only(self):
        config = configuration(); config['devices'][1]['controls'] = {
            'dishwasher_start': True, 'dishwasher': ['sanitize', 'mute']}
        self.assertEqual(validate(config)['devices'][1]['controls']['dishwasher'], ['sanitize', 'mute'])
        for bad in (['nonsense'], ['mute', 'mute'], 'mute', [5], True):
            config = configuration(); config['devices'][1]['controls'] = {'dishwasher': bad}
            with self.subTest(bad=bad), self.assertRaises(ValueError): validate(config)
        config = configuration(); config['devices'][0]['controls'] = {'dishwasher': ['mute']}
        with self.assertRaises(ValueError): validate(config)
        config = configuration(); config['devices'][1]['controls'] = {'dishwasher_start': 'true'}
        with self.assertRaises(ValueError): validate(config)

    def test_button_names_are_accepted_in_the_dishwasher_list(self):
        config = configuration(); config['devices'][1]['controls'] = {'dishwasher': ['pause', 'resume', 'cancel']}
        self.assertEqual(validate(config)['devices'][1]['controls']['dishwasher'], ['pause', 'resume', 'cancel'])

    def test_runtime_export_keeps_the_dishwasher_control_list(self):
        config = configuration(); config['devices'][1]['controls'] = {'dishwasher': ['sanitize', 'door_release']}
        exported = runtime_config(validate(config))['devices'][1]['controls']
        self.assertEqual(exported, {'dishwasher': ['sanitize', 'door_release']})

    def test_registry_restores_two_identities_and_rejects_foreign_or_duplicate_records(self):
        config = configuration(); records = [registration(item) for item in config['devices']]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'registrations.json'; atomic_json(path, records)
            self.assertEqual(load_registry(path, config), records)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            bad = copy.deepcopy(records); bad[1]['serialNumber'] = 'OTHER-DEVICE'; atomic_json(path, bad)
            with self.assertRaises(ValueError): load_registry(path, config)
            atomic_json(path, [records[0], records[0]])
            with self.assertRaises(ValueError): load_registry(path, config)
