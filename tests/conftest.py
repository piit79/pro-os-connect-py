import pytest

import pro_os_connect
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


@pytest.fixture(autouse=True)
def no_readback_delay(monkeypatch):
    monkeypatch.setattr(pro_os_connect, "SETPOINT_READBACK_DELAY", 0)


@pytest.fixture
def without_get_time_response(monkeypatch):
    """Remove the GET_TIME response class, as if a request had been added without one (a library bug)"""
    from pro_os_connect import response
    from pro_os_connect.common import Command

    monkeypatch.delitem(response._registry, Command.GET_TIME)
