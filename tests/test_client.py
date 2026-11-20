import socket
import struct
from datetime import datetime

import pytest

import pro_os_connect
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
from pro_os_connect.common import FRAMING_PHRASE, Command, StatusCode, TelemetryFields
from pro_os_connect.request import GetTimeRequest
from pro_os_connect.response import (
    ResponseCommandNotImplementedError,
    ResponseFramingPhraseError,
    ResponseInternalError,
    ResponsePayloadCrcError,
)
from pro_os_connect.util import calculate_crc

from .helpers import build_packet, parse_request

# Payload CRC of a GET_TIME response with payload b"abcd" (covers the protocol header and payload)
GET_TIME_ABCD_CRC = calculate_crc(build_packet(Command.GET_TIME, b"abcd")[16:])


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

    def test_keep_connection_default(self):
        assert ProOSConnect("example.invalid").keep_connection is True


class TestWithoutKeepConnection:
    @pytest.fixture
    def client(self, device):
        poc = ProOSConnect("127.0.0.1", device.port, timeout=0.3, keep_connection=False)
        yield poc
        poc.disconnect()

    def test_disconnects_after_request(self, client, simulated, device):
        assert client.get_protocol_version() == 1
        assert client.socket is None

    def test_new_connection_per_request(self, client, simulated, device):
        client.get_protocol_version()
        client.get_software_version()
        client.get_serial_number()
        assert device.connections == 3
        assert sent_commands(device) == [
            Command.GET_PROTOCOL_VERSION,
            Command.GET_SOFTWARE_VERSION,
            Command.GET_SERIAL_NUMBER,
        ]

    def test_set_setpoint_reads_back_on_same_connection(self, client, simulated, device):
        assert client.set_setpoint(120) == 120
        assert device.connections == 1
        assert sent_commands(device) == [Command.SET_SETPOINT, Command.GET_TELEMETRY]
        assert client.socket is None

    def test_disconnects_after_error(self, client, simulated, device):
        simulated.status = StatusCode.WRITE_PROTECTED
        with pytest.raises(ProOSConnectResponseError):
            client.set_setpoint(120)
        assert client.socket is None


class TestHoldConnection:
    @pytest.fixture
    def client(self, device):
        poc = ProOSConnect("127.0.0.1", device.port, timeout=0.3, keep_connection=False)
        yield poc
        poc.disconnect()

    def test_one_connection_in_block(self, client, simulated, device):
        with client.hold_connection():
            client.get_protocol_version()
            client.get_software_version()
            assert client.socket is not None
        assert client.socket is None
        assert device.connections == 1
        assert sent_commands(device) == [Command.GET_PROTOCOL_VERSION, Command.GET_SOFTWARE_VERSION]

    def test_yields_client(self, client):
        with client.hold_connection() as poc:
            assert poc is client

    def test_connects_lazily(self, client, simulated, device):
        with client.hold_connection():
            assert client.socket is None
        assert device.connections == 0

    def test_disconnects_after_block_again(self, client, simulated, device):
        with client.hold_connection():
            client.get_protocol_version()
        client.get_protocol_version()
        assert client.socket is None
        assert device.connections == 2

    def test_nested_blocks(self, client, simulated, device):
        with client.hold_connection():
            with client.hold_connection():
                client.get_protocol_version()
            assert client.socket is not None  # Inner block must not close the outer block's connection
            client.set_setpoint(120)  # Holds the connection internally too
            assert client.socket is not None
        assert client.socket is None
        assert device.connections == 1

    def test_reconnects_after_error_in_block(self, client, simulated, device):
        with client.hold_connection():
            simulated.status = StatusCode.WRITE_PROTECTED
            with pytest.raises(ProOSConnectResponseError):
                client.get_protocol_version()
            assert client.socket is None
            simulated.status = StatusCode.OK
            client.get_protocol_version()
            assert client.socket is not None
        assert client.socket is None
        assert device.connections == 2

    def test_disconnects_when_block_raises(self, client, simulated, device):
        with pytest.raises(RuntimeError), client.hold_connection():
            client.get_protocol_version()
            raise RuntimeError("caller error")
        assert client.socket is None

    def test_no_effect_with_keep_connection(self, device, simulated):
        poc = ProOSConnect("127.0.0.1", device.port, timeout=0.3)
        try:
            with poc.hold_connection():
                poc.get_protocol_version()
            assert poc.socket is not None
            poc.get_software_version()
            assert device.connections == 1
        finally:
            poc.disconnect()


class TestCommands:
    def test_get_protocol_version(self, client, simulated):
        assert client.get_protocol_version() == 1

    @pytest.mark.parametrize("payload", [b"", b"A", b"AB", b"ABC", b"ABCD", b"ABCDE", bytes(range(256))])
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
        assert client.get_network_configuration() == {
            "manual": {},
            "write_enabled": True,
        }

    def test_get_setpoint(self, client, simulated):
        assert client.get_setpoint() == 150

    @pytest.mark.parametrize(
        ("setpoint", "read_back", "expected_result", "expected_setpoint"),
        [(50, True, 100, 100), (150, True, 150, 150), (300, True, 200, 200), (120, False, None, 120)],
    )
    def test_set_setpoint(self, client, simulated, device, setpoint, read_back, expected_result, expected_setpoint):
        assert client.set_setpoint(setpoint, read_back=read_back) == expected_result
        index = -2 if read_back else -1  # With read_back=True, -2 is set_setpoint, -1 is get_telemetry
        assert parse_request(device.requests[index])[3] == struct.pack("<I", setpoint)
        assert simulated.setpoint == expected_setpoint
        assert client.get_telemetry().setpoint == expected_setpoint

    def test_send_request_returns_response(self, client, simulated):
        response = client.send_request(GetTimeRequest())
        assert response.status is StatusCode.OK
        assert response.result == datetime(2026, 10, 27, 19, 27, 46)


class TestSetpointReadBackDelay:
    DELAY = 0.123  # Distinctive value; the autouse fixture would otherwise make it 0

    @pytest.fixture
    def sleeps(self, monkeypatch, device):
        """Record each sleep together with the commands the device had received at that moment"""
        calls = []
        monkeypatch.setattr(pro_os_connect, "SETPOINT_READBACK_DELAY", self.DELAY)
        monkeypatch.setattr(pro_os_connect.time, "sleep", lambda s: calls.append((s, sent_commands(device))))
        return calls

    def test_waits_between_set_and_read_back(self, client, simulated, device, sleeps):
        client.set_setpoint(120)
        # Slept exactly once, after the set request and before the telemetry read-back
        assert sleeps == [(self.DELAY, [Command.SET_SETPOINT])]
        assert sent_commands(device) == [Command.SET_SETPOINT, Command.GET_TELEMETRY]

    def test_no_wait_without_read_back(self, client, simulated, device, sleeps):
        client.set_setpoint(120, read_back=False)
        assert sleeps == []
        assert sent_commands(device) == [Command.SET_SETPOINT]

    def test_no_wait_when_set_fails(self, client, simulated, device, sleeps):
        simulated.status = StatusCode.WRITE_PROTECTED
        with pytest.raises(ProOSConnectResponseError):
            client.set_setpoint(120)
        assert sleeps == []
        assert sent_commands(device) == [Command.SET_SETPOINT]


class TestMismatchedResponse:
    @pytest.mark.parametrize(
        ("method", "args", "command"),
        [
            ("get_software_version", (), Command.GET_SOFTWARE_VERSION),
            ("get_serial_number", (), Command.GET_SERIAL_NUMBER),
            ("set_time", (datetime(2026, 11, 13),), Command.SET_TIME),
            ("pump_start", (), Command.PUMP_START),
            ("set_setpoint", (200,), Command.SET_SETPOINT),
        ],
    )
    def test_mismatched_command_raises(self, client, device, method, args, command):
        # A response for a different command than requested
        device.respond(build_packet(Command.GET_PROTOCOL_VERSION, struct.pack("<I", 1)))
        with pytest.raises(
            ProOSConnectResponseError,
            match=f"Response command GET_PROTOCOL_VERSION does not match request {command.name}",
        ):
            getattr(client, method)(*args)


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
            (
                build_packet(Command.GET_TIME, framing_phrase=0x12345678),
                "framing phrase",
            ),
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
        "status",
        [s for s in StatusCode if s not in (StatusCode.NONE, StatusCode.OK)],
        ids=lambda s: s.name,
    )
    def test_error_status(self, client, simulated, status):
        simulated.status = status
        with pytest.raises(ProOSConnectResponseError, match=status.name):
            client.pump_start()
        assert client.socket is None

    def test_recovers_after_error(self, client, simulated, device):
        simulated.status = StatusCode.WRITE_PROTECTED
        with pytest.raises(ProOSConnectResponseError):
            client.set_setpoint(120)
        simulated.status = StatusCode.OK
        assert client.set_setpoint(120) == 120
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


class TestConnect:
    def test_connect_is_idempotent(self, client, device):
        client.connect()
        sock = client.socket
        client.connect()
        assert client.socket is sock

    def test_connect_force_opens_new_connection(self, client, simulated, device):
        client.connect()
        sock = client.socket
        client.connect(force=True)
        assert client.socket is not sock
        sock.close()  # The fake device serves one connection at a time
        assert client.get_protocol_version() == 1
        assert device.connections == 2

    def test_connect_force_closes_old_socket(self, client, device):
        client.connect()
        sock = client.socket
        client.connect(force=True)
        try:
            assert sock.fileno() == -1
        finally:
            sock.close()

    def test_successful_request_keeps_connection(self, client, simulated, caplog):
        with caplog.at_level("DEBUG", logger="pro_os_connect"):
            client.get_time()
        assert client.socket is not None
        assert "response received successfully" in caplog.text


class TestErrorDetails:
    @pytest.mark.parametrize(
        ("packet", "cause", "message"),
        [
            (
                build_packet(Command.GET_TIME, framing_phrase=0x12345678),
                ResponseFramingPhraseError,
                "Invalid framing phrase: 0x12345678",
            ),
            (
                build_packet(Command.GET_TIME, b"abcd", payload_crc=1),
                ResponsePayloadCrcError,
                f"Response payload CRC error: received=0x1 calculated=0x{GET_TIME_ABCD_CRC:x}",
            ),
            (
                build_packet(Command.GET_TIME, status=0x12345677),
                ResponseInternalError,
                f"Response internal error: Invalid value: {0x12345677} is not a valid StatusCode",
            ),
        ],
        ids=["framing", "payload-crc", "unknown-status"],
    )
    def test_response_error_message_and_cause(self, client, device, packet, cause, message):
        device.respond(packet)
        with pytest.raises(ProOSConnectResponseError) as exc_info:
            client.get_time()
        assert str(exc_info.value) == message
        assert type(exc_info.value.__cause__) is cause
        assert client.socket is None

    def test_internal_error(self, client, device):
        device.respond(build_packet(Command.SOFTWARE_UPDATE_START))
        with pytest.raises(
            ProOSConnectInternalError,
            match="Response command SOFTWARE_UPDATE_START not implemented",
        ) as exc_info:
            client.get_time()
        assert isinstance(exc_info.value, ProOSConnectError)
        assert not isinstance(exc_info.value, (ProOSConnectRequestError, ProOSConnectResponseError))
        assert type(exc_info.value.__cause__) is ResponseCommandNotImplementedError

    def test_recovers_after_internal_error(self, client, simulated, device):
        handler = device.handler
        device.respond(build_packet(Command.SOFTWARE_UPDATE_START))
        with pytest.raises(ProOSConnectInternalError):
            client.get_time()
        device.handler = handler
        assert client.get_protocol_version() == 1
        assert device.connections == 2

    def test_invalid_request_argument_disconnects(self, client, simulated, device):
        with pytest.raises(struct.error):
            client.set_setpoint(-1)
        assert client.socket is None
        assert device.requests == []


def build_raw_packet(protocol_packet: bytes) -> bytes:
    """Wrap an arbitrary protocol packet in a valid transport header"""
    header = struct.pack("<III", FRAMING_PHRASE, len(protocol_packet), calculate_crc(protocol_packet))
    return header + struct.pack("<I", calculate_crc(header)) + protocol_packet


class TestMalformedResponses:
    """A well-framed response with contents the library can't decode should raise a ProOSConnectError"""

    def test_unknown_command_code(self, client, device):
        cmd = 0x0001FFFF
        device.respond(build_packet(cmd))
        with pytest.raises(
            ProOSConnectResponseError,
            match=f"Response internal error: Invalid value: {cmd} is not a valid Command",
        ):
            client.get_time()

    def test_unknown_node_address(self, client, device):
        device.respond(build_raw_packet(struct.pack("<IIHH", Command.GET_TIME.value, StatusCode.OK.value, 9, 3)))
        with pytest.raises(
            ProOSConnectResponseError,
            match="Response internal error: Invalid value: 9 is not a valid Node",
        ):
            client.get_time()

    def test_short_payload(self, client, device):
        device.respond(build_packet(Command.GET_PROTOCOL_VERSION, b"\x01"))
        with pytest.raises(
            ProOSConnectResponseError,
            match="Error decoding response: unpack requires a buffer of 4 bytes",
        ):
            client.get_protocol_version()

    def test_invalid_utf8(self, client, device):
        device.respond(build_packet(Command.GET_SOFTWARE_VERSION, b"\xff\x00"))
        with pytest.raises(
            ProOSConnectResponseError,
            match="Error decoding response: 'utf-8' codec can't decode byte 0xff in position 0: invalid start byte",
        ):
            client.get_software_version()

    def test_invalid_json(self, client, device):
        device.respond(build_packet(Command.GET_NETWORK_CONFIGURATION, b"{oops\x00"))
        with pytest.raises(
            ProOSConnectResponseError,
            match=r"Error decoding response: Expecting property name enclosed in double quotes: line 1 column 2 \(char 1\)",
        ):
            client.get_network_configuration()

    def test_invalid_time(self, client, device):
        device.respond(build_packet(Command.GET_TIME, b"yesterday\x00"))
        with pytest.raises(
            ProOSConnectResponseError,
            match="Error decoding response: Invalid isoformat string: 'yesterday'",
        ):
            client.get_time()

    @pytest.mark.parametrize(
        ("packet", "method"),
        [
            (build_packet(0x0001FFFF), "get_time"),
            (
                build_packet(Command.GET_PROTOCOL_VERSION, b"\x01"),
                "get_protocol_version",
            ),
            (
                build_packet(Command.GET_NETWORK_CONFIGURATION, b"{oops\x00"),
                "get_network_configuration",
            ),
        ],
        ids=["unknown-command", "short-payload", "invalid-json"],
    )
    def test_client_disconnects(self, client, device, packet, method):
        device.respond(packet)
        with pytest.raises(Exception):  # noqa: B017 - the type is covered by the xfails above
            getattr(client, method)()
        assert client.socket is None
