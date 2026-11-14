import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from fixtures import configuration


def platform(name):
    return patch('thingslocal.network_select.sys', SimpleNamespace(platform=name))


class SelectionTests(unittest.TestCase):
    def test_darwin_uses_the_macos_backend(self):
        from thingslocal import network_macos, network_select
        with platform('darwin'):
            self.assertIs(network_select.backend(), network_macos)

    def test_linux_and_other_systems_use_the_networkmanager_backend(self):
        from thingslocal import network, network_select
        for name in ('linux', 'win32', 'freebsd14'):
            with platform(name):
                self.assertIs(network_select.backend(), network)


class ScanTests(unittest.TestCase):
    def test_macos_scan_is_unsupported_and_points_to_the_wifi_menu(self):
        from thingslocal.network_macos import ScanUnsupported, scan
        with self.assertRaises(ScanUnsupported) as caught:
            scan(configuration()['devices'][0]['identity'])
        self.assertIn('not supported on macOS', str(caught.exception))
        self.assertIn('Wi-Fi menu', str(caught.exception))
        self.assertIsInstance(caught.exception, ValueError)

    def test_scan_command_on_macos_prints_a_friendly_result(self):
        from thingslocal import cli
        config = configuration()
        out = io.StringIO()
        with TemporaryDirectory() as workspace, platform('darwin'), \
                patch('sys.argv', ['thingslocal', '--workspace', workspace, 'scan', 'hob']), \
                patch('thingslocal.cli.read_config', return_value=config), redirect_stdout(out):
            code = cli.main()
            self.assertEqual(list(Path(workspace).iterdir()), [])
        result = json.loads(out.getvalue())
        self.assertEqual(code, 0)
        self.assertFalse(result['supported'])
        self.assertIn('Wi-Fi menu', result['message'])


class PairingFixture(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.workspace = Path(self.directory.name)
        self.config = configuration()
        self.device = self.config['devices'][0]
        (self.workspace / 'setup-material').mkdir()
        (self.workspace / 'setup-material' / 'hob.json').write_text(
            json.dumps({'identity': self.device['identity']}))
        self.network = Mock()
        self.network.__enter__ = Mock(return_value=self.network)
        self.network.__exit__ = Mock(return_value=None)
        self.network.gateway = '192.0.2.1'
        self.network.restored = True
        del self.network.not_restored_message

    def pair(self):
        from thingslocal import provision
        backend = SimpleNamespace(APSession=Mock(return_value=self.network),
                                  scan=Mock(side_effect=self.unsupported))
        with patch('thingslocal.provision.verify_certificate'), \
                patch('thingslocal.provision.verify_route', return_value='ssl://bridge.example.test:8883'), \
                patch('thingslocal.provision.network_select.backend', return_value=backend), \
                patch('thingslocal.provision.SetupSession') as setup:
            self.setup = setup
            try:
                return provision.pair(self.workspace, self.config, self.device)
            finally:
                self.backend = backend


class PairOnMacTests(PairingFixture):
    def unsupported(self, identity):
        from thingslocal.network_macos import ScanUnsupported
        raise ScanUnsupported('not supported on macOS')

    def test_pairing_without_a_scan_succeeds_and_reports_restoration(self):
        result = self.pair()
        self.backend.APSession.assert_called_once_with(None, None)
        self.setup.assert_called_once()
        self.assertEqual(self.setup.call_args.args[0], '192.0.2.1')
        self.assertTrue(result['configured'])
        self.assertTrue(result['network_restored'])

    def test_unreachable_appliance_sends_nothing(self):
        self.network.__enter__ = Mock(side_effect=ValueError('Nothing answered on port 8888'))
        with self.assertRaisesRegex(ValueError, '8888'):
            self.pair()
        self.setup.assert_not_called()

    def test_wifi_not_back_reports_a_macos_message_not_a_systemd_one(self):
        self.network.restored = False
        self.network.not_restored_message = 'Choose your usual Wi-Fi from the Wi-Fi menu.'
        with self.assertRaisesRegex(ValueError, 'Wi-Fi menu'):
            self.pair()
        saved = json.loads((self.workspace / 'pairing-hob.json').read_text())
        self.assertTrue(saved['configured'])
        self.assertFalse(saved['network_restored'])


class PairOnLinuxTests(PairingFixture):
    found = []

    def unsupported(self, identity):
        return self.found

    def test_no_access_point_stops_before_any_session_is_made(self):
        with self.assertRaisesRegex(ValueError, 'No matching appliance AP'):
            self.pair()
        self.backend.APSession.assert_not_called()

    def test_found_access_point_is_passed_to_the_session_as_before(self):
        ap = {'ssid': 'Fixture_AP', 'bssid': '02:00:00:00:00:02', 'signal': 50}
        self.found = [ap]
        self.pair()
        self.backend.APSession.assert_called_once_with(ap, None)


if __name__ == '__main__':
    unittest.main()
