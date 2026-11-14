"""macOS pairing network: the person joins the appliance's Wi-Fi by hand."""
import ipaddress
import re
import socket
import subprocess
import sys
import time

WIFI_PORT_NAMES = ('Wi-Fi', 'AirPort')
SETUP_PORT = 8888
SETUP_PASSWORD = '1111122222'
JOIN_WAIT = 30
POLL_SECONDS = 2
RESTORE_WAIT = 60


SCAN_MESSAGE = (
    "Scanning is not supported on macOS. Look for the appliance's network "
    'in the Wi-Fi menu instead.')


class ScanUnsupported(ValueError):
    pass


def scan(identity):
    raise ScanUnsupported(SCAN_MESSAGE)


def say(text):
    print(text, file=sys.stderr)


def command(*args, timeout=10):
    return subprocess.run(args, check=False, capture_output=True, text=True, timeout=timeout)


def wifi_interfaces():
    output = command('networksetup', '-listallhardwareports').stdout
    found = []
    for block in output.split('\n\n'):
        port = re.search(r'^Hardware Port:\s*(.+)$', block, re.MULTILINE)
        device = re.search(r'^Device:\s*(\S+)$', block, re.MULTILINE)
        if port and device and port[1].strip() in WIFI_PORT_NAMES:
            found.append(device[1])
    return found


def valid_address(text):
    try:
        return str(ipaddress.IPv4Address(text.strip()))
    except ValueError:
        return None


def router(interface):
    result = command('ipconfig', 'getoption', interface, 'router')
    if result.returncode != 0:
        return None
    return valid_address(result.stdout)


def default_gateway():
    result = command('route', '-n', 'get', 'default')
    if result.returncode != 0:
        return None
    match = re.search(r'^\s*gateway:\s*(\S+)\s*$', result.stdout, re.MULTILINE)
    return valid_address(match[1]) if match else None


def reachable(host, port, timeout=3):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class APSession:
    """Waits for the person to join the appliance's Wi-Fi, then finds its gateway."""

    not_restored_message = (
        'Pairing finished, but your normal Wi-Fi has not come back. '
        'Choose your usual network from the Wi-Fi menu.')

    def __init__(self, network=None, interface=None):
        self.network, self.interface = network, interface
        self.gateway = None
        self.restored = False

    def choose_interface(self):
        found = wifi_interfaces()
        if self.interface:
            if self.interface not in found:
                raise ValueError(f'{self.interface} is not a Wi-Fi interface on this Mac')
            return self.interface
        if not found:
            raise ValueError('No Wi-Fi interface found on this Mac')
        if len(found) > 1:
            raise ValueError('Select exactly one Wi-Fi interface with --interface')
        return found[0]

    def wait_for_gateway(self):
        deadline = time.monotonic() + JOIN_WAIT
        problem = 'No router address was found on the Wi-Fi interface'
        while True:
            address = router(self.interface)
            if address and reachable(address, SETUP_PORT):
                return address
            if address:
                problem = f'Nothing answered at {address} on port {SETUP_PORT}'
            if time.monotonic() >= deadline:
                break
            time.sleep(POLL_SECONDS)
        raise ValueError(
            problem + '. This does not look like the appliance. '
            'Join its setup network and try again. Nothing was sent.')

    def __enter__(self):
        self.interface = self.choose_interface()
        say("Join the appliance's setup network from the Wi-Fi menu.")
        say(f'If asked for a password, use {SETUP_PASSWORD}.')
        say('Your Mac may warn that the network has no internet. That is expected.')
        input('Press Enter when you have joined it. ')
        self.gateway = self.wait_for_gateway()
        return self

    def back_on_normal_network(self):
        default = default_gateway()
        if default is None or default == self.gateway:
            return False
        return router(self.interface) != self.gateway

    def wait_for_normal_network(self):
        deadline = time.monotonic() + RESTORE_WAIT
        while not self.back_on_normal_network():
            if time.monotonic() >= deadline:
                return False
            time.sleep(POLL_SECONDS)
        return True

    def __exit__(self, exc_type, *unused):
        if self.gateway is None:
            self.restored = True
            return
        if exc_type is None:
            say('Pairing finished. Waiting for your normal network to come back...')
            self.restored = self.wait_for_normal_network()
        if self.restored:
            say('Your normal network is back.')
        else:
            say('Your normal network is not back. '
                'Choose your usual Wi-Fi from the Wi-Fi menu.')
