import pytest

from pro_os_connect import ProOSConnect

from .helpers import FakeDevice, SimulatedDevice


@pytest.fixture
def device():
    dev = FakeDevice()
    yield dev
    dev.close()


@pytest.fixture
def client(device):
    poc = ProOSConnect("127.0.0.1", device.port, timeout=0.3)
    yield poc
    poc.disconnect()


@pytest.fixture
def simulated(device):
    """Fake device answering like a real one; returns the state for inspection"""
    state = SimulatedDevice()
    device.handler = state
    return state
