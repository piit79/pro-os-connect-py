import socket
import struct
from datetime import datetime

import pytest

from pro_os_connect import (
    TCP_PORT,
    ProOSConnect,
    ProOSConnectConnectError,
    ProOSConnectError,
    ProOSConnectInternalError,
    ProOSConnectRequestError,
    ProOSConnectResponseError,
    ProOSConnectTimeoutError,
)
from pro_os_connect.common import Command, StatusCode, TelemetryFields
from pro_os_connect.request import GetTimeRequest

from .helpers import build_packet, parse_request


def sent_commands(device) -> list[Command]:
    return [parse_request(r)[0] for r in device.requests]


class TestErrorHierarchy:
    @pytest.mark.parametrize(
        ("error", "parent"),
        [
            (ProOSConnectRequestError, ProOSConnectError),
            (ProOSConnectConnectError, ProOSConnectRequestError),
            (ProOSConnectTimeoutError, ProOSConnectRequestError),
            (ProOSConnectResponseError, ProOSConnectError),
        ],
    )
    def test_subclass(self, error, parent):
        assert issubclass(error, parent)

    def test_response_error_is_not_request_error(self):
        assert not issubclass(ProOSConnectResponseError, ProOSConnectRequestError)


class TestConnection:
    def test_defaults(self):
        poc = ProOSConnect("example.invalid")
        assert poc.port == TCP_PORT == 2345
        assert poc.timeout == 2.0
        assert poc.socket is None

    def test_connect_and_disconnect(self, client):
        client.connect()
        assert client.socket is not None
        assert client.socket.getblocking() is False
        client.disconnect()
        assert client.socket is None

    def test_disconnect_when_not_connected(self, client):
        client.disconnect()
        assert client.socket is None

    def test_connection_refused(self):
        with socket.create_server(("127.0.0.1", 0)) as server:
            port = server.getsockname()[1]
        poc = ProOSConnect("127.0.0.1", port, timeout=0.3)
        with pytest.raises(ProOSConnectConnectError) as exc_info:
            poc.connect()
        assert isinstance(exc_info.value.__cause__, OSError)
        assert poc.socket is None

    def test_unresolvable_host(self):
        poc = ProOSConnect("host.invalid", timeout=0.3)
        with pytest.raises(ProOSConnectConnectError):
            poc.get_protocol_version()

    def test_send_request_connects_lazily(self, client, simulated, device):
        assert client.socket is None
        assert client.get_protocol_version() == 1
        assert client.socket is not None

    def test_connection_is_reused(self, client, simulated, device):
        client.get_protocol_version()
        client.get_software_version()
        client.get_serial_number()
        assert device.connections == 1
        assert sent_commands(device) == [
            Command.GET_PROTOCOL_VERSION,
            Command.GET_SOFTWARE_VERSION,
            Command.GET_SERIAL_NUMBER,
        ]


class TestCommands:
    def test_get_protocol_version(self, client, simulated):
        assert client.get_protocol_version() == 1

    @pytest.mark.parametrize(
        "payload", [b"", b"A", b"AB", b"ABC", b"ABCD", b"ABCDE", bytes(range(256))]
    )
    def test_echo(self, client, simulated, device, payload):
        assert client.echo(payload) is True
        assert parse_request(device.requests[-1])[3] == payload

    def test_echo_default_payload(self, client, simulated, device):
        assert client.echo() is True
        assert parse_request(device.requests[-1])[3] == b"Test Echo Payload"

    def test_echo_mismatch(self, client, device):
        device.respond(build_packet(Command.ECHO, b"something else"))
        assert client.echo(b"ping") is False

    def test_reboot(self, client, simulated, device):
        device.respond(build_packet(Command.REBOOT))
        assert client.reboot() is True
        assert sent_commands(device) == [Command.REBOOT]

    def test_get_telemetry(self, client, simulated):
        result = client.get_telemetry()
        assert isinstance(result, TelemetryFields)
        assert result.setpoint == 150
        assert result.flow_rate == pytest.approx(4.95712)
        assert result.is_running == 0

    def test_get_uptime(self, client, simulated):
        assert client.get_uptime() == 103627

    @pytest.mark.parametrize("running", [False, True])
    def test_is_running(self, client, simulated, running):
        simulated.running = running
        assert client.is_running() is running

    def test_get_time(self, client, simulated):
        assert client.get_time() == datetime(2026, 10, 27, 19, 27, 46)

    def test_set_time(self, client, simulated, device):
        assert client.set_time(datetime(2025, 1, 2, 3, 4, 5, 678)) is True
        assert parse_request(device.requests[-1])[3] == b"2025-01-02T03:04:05\x00"
        assert client.get_time() == datetime(2025, 1, 2, 3, 4, 5)

    def test_pump_start_and_stop(self, client, simulated):
        assert client.pump_start() is True
        assert simulated.running
        assert client.is_running() is True
        assert client.pump_stop() is True
        assert not simulated.running
        assert client.is_running() is False

    def test_get_software_version(self, client, simulated):
        assert client.get_software_version() == "v2.0.0"

    def test_get_serial_number(self, client, simulated):
        assert client.get_serial_number() == "2511000618"

    def test_get_cpu_serial_number(self, client, simulated):
        assert client.get_cpu_serial_number() == "5767072d39314637424e79137d2e5454"

    def test_get_network_configuration(self, client, simulated):
        assert client.get_network_configuration() == {"manual": {}, "write_enabled": True}

    def test_set_setpoint(self, client, simulated, device):
        assert client.set_setpoint(175) is True
        assert parse_request(device.requests[-1])[3] == struct.pack("<I", 175)
        assert simulated.setpoint == 175
        assert client.get_telemetry().setpoint == 175

    def test_send_request_returns_response(self, client, simulated):
        response = client.send_request(GetTimeRequest())
        assert response.status is StatusCode.OK
        assert response.result == datetime(2026, 10, 27, 19, 27, 46)


class TestWrongResultType:
    """Getters return None when the device answers with an unexpected result type"""

    @pytest.mark.parametrize(
        ("method", "command"),
        [
            ("get_software_version", Command.GET_SOFTWARE_VERSION),
            ("get_serial_number", Command.GET_SERIAL_NUMBER),
        ],
    )
    def test_string_getters(self, client, device, method, command):
        # A response for a different command than requested
        device.respond(build_packet(Command.GET_PROTOCOL_VERSION, struct.pack("<I", 1)))
        assert getattr(client, method)() is None

    def test_get_protocol_version(self, client, device):
        device.respond(build_packet(Command.GET_SOFTWARE_VERSION, b"v1\x00"))
        assert client.get_protocol_version() is None

    def test_get_time(self, client, device):
        device.respond(build_packet(Command.GET_SERIAL_NUMBER, b"123\x00"))
        assert client.get_time() is None

    @pytest.mark.parametrize("method", ["get_telemetry", "get_uptime", "is_running"])
    def test_telemetry_based(self, client, device, method):
        device.respond(build_packet(Command.GET_SERIAL_NUMBER, b"123\x00"))
        assert getattr(client, method)() is None


class TestFraming:
    def test_response_split_into_chunks(self, client, device):
        packet = build_packet(Command.GET_SOFTWARE_VERSION, b"v2.0.0\x00")
        device.respond(packet[:3], packet[3:16], packet[16:20], packet[20:])
        assert client.get_software_version() == "v2.0.0"

    def test_response_byte_by_byte(self, client, device):
        packet = build_packet(Command.GET_SERIAL_NUMBER, b"2511000618\x00")
        device.respond(*(packet[i : i + 1] for i in range(len(packet))))
        assert client.get_serial_number() == "2511000618"

    def test_large_response(self, client, device):
        payload = bytes(range(256)) * 40
        device.respond(build_packet(Command.ECHO, payload))
        assert client.echo(payload) is True


class TestErrors:
    def test_timeout_without_response(self, client, device):
        device.respond()
        with pytest.raises(ProOSConnectTimeoutError, match="Timeout"):
            client.get_time()
        assert client.socket is None

    def test_truncated_payload(self, client, device):
        device.respond(build_packet(Command.GET_TIME, b"2026-10-27T19:27:46\x00")[:-5])
        with pytest.raises(ProOSConnectResponseError, match="length incorrect"):
            client.get_time()
        assert client.socket is None

    def test_incomplete_header(self, client, device):
        device.respond(build_packet(Command.GET_TIME)[:10])
        with pytest.raises(ProOSConnectResponseError, match="incomplete"):
            client.get_time()

    def test_connection_closed_by_device(self, client, device):
        device.handler = lambda request: None
        with pytest.raises(ProOSConnectConnectError, match="closed"):
            client.get_time()
        assert client.socket is None

    @pytest.mark.parametrize(
        ("packet", "message"),
        [
            (build_packet(Command.GET_TIME, framing_phrase=0x12345678), "framing phrase"),
            (build_packet(Command.GET_TIME, header_crc=1), "header CRC"),
            (build_packet(Command.GET_TIME, b"abcd", payload_crc=1), "payload CRC"),
            (build_packet(Command.GET_TIME) + b"junk", "length"),
        ],
        ids=["framing", "header-crc", "payload-crc", "trailing-data"],
    )
    def test_invalid_packet(self, client, device, packet, message, caplog):
        device.respond(packet)
        with pytest.raises(ProOSConnectResponseError, match=message):
            client.get_time()
        assert client.socket is None

    @pytest.mark.parametrize(
        "status", [s for s in StatusCode if s not in (StatusCode.NONE, StatusCode.OK)], ids=lambda s: s.name
    )
    def test_error_status(self, client, simulated, status):
        simulated.status = status
        with pytest.raises(ProOSConnectResponseError, match=status.name):
            client.pump_start()
        assert client.socket is None

    def test_recovers_after_error(self, client, simulated, device):
        simulated.status = StatusCode.WRITE_PROTECTED
        with pytest.raises(ProOSConnectResponseError):
            client.set_setpoint(100)
        simulated.status = StatusCode.OK
        assert client.set_setpoint(100) is True
        assert device.connections == 2

    def test_recovers_after_timeout(self, client, simulated, device):
        handler = device.handler
        device.respond()
        with pytest.raises(ProOSConnectTimeoutError):
            client.get_time()
        device.handler = handler
        assert client.get_protocol_version() == 1
        assert device.connections == 2

    def test_os_error_while_sending(self, client, simulated):
        client.connect()
        real_socket = client.socket

        class BrokenSocket:
            def sendall(self, data):
                raise BrokenPipeError("broken pipe")

            def close(self):
                real_socket.close()

        client.socket = BrokenSocket()
        with pytest.raises(ProOSConnectConnectError) as exc_info:
            client.get_time()
        assert isinstance(exc_info.value.__cause__, BrokenPipeError)
        assert client.socket is None

    def test_unexpected_command_in_response(self, client, device):
        device.respond(build_packet(Command.SOFTWARE_UPDATE_START))
        with pytest.raises(ProOSConnectInternalError):
            client.get_time()
        assert client.socket is None

    def test_unknown_status(self, client, device):
        device.respond(build_packet(Command.GET_TIME, status=0x12345678))
        with pytest.raises(ProOSConnectResponseError):
            client.get_time()
