from types import SimpleNamespace
import unittest
from unittest.mock import patch

HARDWARE_PORTS = '''
Hardware Port: Ethernet Adapter (en4)
Device: en4
Ethernet Address: 02:00:00:00:00:04

Hardware Port: Wi-Fi
Device: en0
Ethernet Address: 02:00:00:00:00:01

Hardware Port: Thunderbolt Bridge
Device: bridge0
Ethernet Address: N/A

VLAN Configurations
===================
'''
NO_WIFI_PORTS = '''
Hardware Port: Ethernet Adapter (en4)
Device: en4
Ethernet Address: 02:00:00:00:00:04
'''
DEFAULT_ROUTE = '''   route to: default
destination: default
       mask: default
    gateway: {gateway}
  interface: en0
      flags: <UP,GATEWAY,DONE,STATIC,PRCLONING,GLOBAL>
'''
NO_ROUTE = 'route: writing to routing socket: not in table\n'


def done(stdout='', returncode=0):
    return SimpleNamespace(stdout=stdout, returncode=returncode)


class InterfaceTests(unittest.TestCase):
    def test_wifi_interface_is_found_in_networksetup_output(self):
        from thingslocal import network_macos
        with patch('thingslocal.network_macos.command', return_value=done(HARDWARE_PORTS)) as run:
            self.assertEqual(network_macos.wifi_interfaces(), ['en0'])
        self.assertEqual(run.call_args.args, ('networksetup', '-listallhardwareports'))

    def test_no_wifi_interface_gives_empty_list(self):
        from thingslocal import network_macos
        with patch('thingslocal.network_macos.command', return_value=done(NO_WIFI_PORTS)):
            self.assertEqual(network_macos.wifi_interfaces(), [])


class GatewayTests(unittest.TestCase):
    def test_router_comes_from_ipconfig_for_the_interface(self):
        from thingslocal import network_macos
        with patch('thingslocal.network_macos.command', return_value=done('192.0.2.1\n')) as run:
            self.assertEqual(network_macos.router('en0'), '192.0.2.1')
        self.assertEqual(run.call_args.args, ('ipconfig', 'getoption', 'en0', 'router'))

    def test_missing_router_gives_none(self):
        from thingslocal import network_macos
        with patch('thingslocal.network_macos.command', return_value=done('', 1)):
            self.assertIsNone(network_macos.router('en0'))

    def test_router_that_is_not_an_address_gives_none(self):
        from thingslocal import network_macos
        with patch('thingslocal.network_macos.command', return_value=done('not an address\n')):
            self.assertIsNone(network_macos.router('en0'))

    def test_default_gateway_is_read_from_route_output(self):
        from thingslocal import network_macos
        with patch('thingslocal.network_macos.command',
                   return_value=done(DEFAULT_ROUTE.format(gateway='198.51.100.1'))) as run:
            self.assertEqual(network_macos.default_gateway(), '198.51.100.1')
        self.assertEqual(run.call_args.args, ('route', '-n', 'get', 'default'))

    def test_no_default_route_gives_none(self):
        from thingslocal import network_macos
        with patch('thingslocal.network_macos.command', return_value=done(NO_ROUTE, 1)):
            self.assertIsNone(network_macos.default_gateway())


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def session_patches(wifi=('en0',), routers=('192.0.2.1',), reachable=True):
    """Patch every outside call; the fake clock makes waiting instant."""
    clock = FakeClock()
    return clock, [
        patch('thingslocal.network_macos.wifi_interfaces', return_value=list(wifi)),
        patch('thingslocal.network_macos.router', side_effect=list(routers) + [routers[-1]] * 100),
        patch('thingslocal.network_macos.reachable', return_value=reachable),
        patch('thingslocal.network_macos.time', clock),
        patch('builtins.input', return_value=''),
        patch('sys.stderr'),
    ]


class EnterTests(unittest.TestCase):
    def enter(self, interface=None, **options):
        from thingslocal.network_macos import APSession
        clock, patches = session_patches(**options)
        mocks = [item.start() for item in patches]
        self.addCleanup(lambda: [item.stop() for item in patches])
        return APSession(None, interface), mocks

    def test_session_exposes_the_gateway_after_the_port_answers(self):
        session, mocks = self.enter()
        self.assertIs(session.__enter__(), session)
        self.assertEqual(session.gateway, '192.0.2.1')
        self.assertEqual(session.interface, 'en0')
        mocks[2].assert_called_with('192.0.2.1', 8888)

    def test_no_wifi_interface_stops_before_asking_the_person_anything(self):
        session, mocks = self.enter(wifi=())
        with self.assertRaisesRegex(ValueError, 'No Wi-Fi interface'):
            session.__enter__()
        mocks[4].assert_not_called()

    def test_several_wifi_interfaces_need_an_explicit_choice(self):
        session, _ = self.enter(wifi=('en0', 'en1'))
        with self.assertRaisesRegex(ValueError, '--interface'):
            session.__enter__()

    def test_named_interface_must_be_a_wifi_interface(self):
        session, _ = self.enter(interface='en9')
        with self.assertRaisesRegex(ValueError, 'en9'):
            session.__enter__()

    def test_missing_gateway_stops_without_touching_the_port(self):
        session, mocks = self.enter(routers=(None,))
        with self.assertRaisesRegex(ValueError, 'router address'):
            session.__enter__()
        mocks[2].assert_not_called()

    def test_gateway_that_appears_late_is_still_found(self):
        session, mocks = self.enter(routers=(None, None, '192.0.2.1'))
        session.__enter__()
        self.assertEqual(session.gateway, '192.0.2.1')

    def test_unreachable_port_stops_with_a_clear_message(self):
        session, _ = self.enter(reachable=False)
        with self.assertRaisesRegex(ValueError, '8888'):
            session.__enter__()

    def test_instructions_include_the_setup_password(self):
        session, _ = self.enter()
        with patch('builtins.print') as shown:
            session.__enter__()
        text = ' '.join(str(call.args[0]) for call in shown.call_args_list)
        self.assertIn('Wi-Fi menu', text)
        self.assertIn('1111122222', text)


class ExitTests(unittest.TestCase):
    def leave(self, gateways, routers=('192.0.2.1',), error=None):
        """Run __exit__ for a session on 192.0.2.1; return it and what was said."""
        from thingslocal.network_macos import APSession
        session = APSession(None, 'en0')
        session.gateway = '192.0.2.1'
        clock = FakeClock()
        with patch('thingslocal.network_macos.time', clock), \
                patch('thingslocal.network_macos.default_gateway',
                      side_effect=list(gateways) + [gateways[-1]] * 100), \
                patch('thingslocal.network_macos.router',
                      side_effect=list(routers) + [routers[-1]] * 100), \
                patch('builtins.print') as shown:
            session.__exit__(*(error or (None, None, None)))
        said = ' '.join(str(call.args[0]) for call in shown.call_args_list)
        return session, said, clock

    def test_restoration_is_detected_when_the_default_route_moves_off_the_appliance(self):
        session, said, _ = self.leave(['192.0.2.1', None, '198.51.100.1'], routers=('198.51.100.1',))
        self.assertTrue(session.restored)
        self.assertIn('normal network is back', said)

    def test_default_route_on_another_interface_is_not_enough_if_wifi_still_on_appliance(self):
        session, said, _ = self.leave(['198.51.100.1'], routers=('192.0.2.1',))
        self.assertFalse(session.restored)

    def test_restoration_timeout_is_bounded_clear_and_not_restored(self):
        session, said, clock = self.leave(['192.0.2.1'])
        self.assertFalse(session.restored)
        self.assertIn('Wi-Fi menu', said)
        self.assertIn('not back', said)
        self.assertLessEqual(clock.now, 70)

    def test_failed_pairing_does_not_wait_and_says_what_to_do(self):
        session, said, clock = self.leave(['192.0.2.1'], error=(ValueError, ValueError('x'), None))
        self.assertFalse(session.restored)
        self.assertEqual(clock.now, 0)
        self.assertIn('Wi-Fi menu', said)

    def test_session_that_never_joined_has_nothing_to_restore(self):
        from thingslocal.network_macos import APSession
        session = APSession(None, 'en0')
        session.__exit__(None, None, None)
        self.assertTrue(session.restored)


if __name__ == '__main__':
    unittest.main()
