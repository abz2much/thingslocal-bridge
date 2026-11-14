"""Chooses the pairing network backend for this computer."""
import sys


def backend():
    if sys.platform == 'darwin':
        from . import network_macos
        return network_macos
    from . import network
    return network
