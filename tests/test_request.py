import struct
from datetime import datetime

import pytest

from pro_os_connect import request as req
from pro_os_connect.common import FRAMING_PHRASE, Command, Node, StatusCode
from pro_os_connect.util import calculate_crc

from .helpers import parse_request

ALL_REQUESTS = [
    (req.GetProtocolVersionRequest, Command.GET_PROTOCOL_VERSION),
    (req.EchoRequest, Command.ECHO),
    (req.RebootRequest, Command.REBOOT),
    (req.GetTelemetryRequest, Command.GET_TELEMETRY),
    (req.GetTimeRequest, Command.GET_TIME),
    (req.SetTimeRequest, Command.SET_TIME),
    (req.PumpStartRequest, Command.PUMP_START),
    (req.PumpStopRequest, Command.PUMP_STOP),
    (req.GetSoftwareVersionRequest, Command.GET_SOFTWARE_VERSION),
    (req.GetSerialNumberRequest, Command.GET_SERIAL_NUMBER),
    (req.GetCpuSerialNumberRequest, Command.GET_CPU_SERIAL_NUMBER),
    (req.GetNetworkConfigurationRequest, Command.GET_NETWORK_CONFIGURATION),
    (req.SetSetpointRequest, Command.SET_SETPOINT),
]
NO_PAYLOAD_REQUESTS = [
    cls for cls, _ in ALL_REQUESTS if cls.CODEC is None
]


@pytest.mark.parametrize(("cls", "command"), ALL_REQUESTS, ids=lambda x: getattr(x, "__name__", ""))
def test_request_command(cls, command):
    assert cls.COMMAND is command


def test_default_addresses():
    request = req.GetTimeRequest()
    assert request.from_addr is Node.CLIENT
    assert request.to_addr is Node.PRO_OS


def test_custom_destination():
    request = req.GetTimeRequest(to_addr=Node.NETWORK_CARD)
    _, from_addr, to_addr, _ = parse_request(request.get_packet())
    assert from_addr is Node.CLIENT
    assert to_addr is Node.NETWORK_CARD


@pytest.mark.parametrize("cls", NO_PAYLOAD_REQUESTS, ids=lambda c: c.__name__)
def test_requests_without_payload(cls):
    packet = cls().get_packet()
    assert len(packet) == 16 + 12
    command, from_addr, to_addr, payload = parse_request(packet)
    assert command is cls.COMMAND
    assert (from_addr, to_addr) == (Node.CLIENT, Node.PRO_OS)
    assert payload == b""


def test_packet_layout_byte_for_byte():
    packet = req.GetProtocolVersionRequest().get_packet()
    protocol_packet = struct.pack("<IIHH", 0x00010001, 0x0FFFFFFF, 3, 1)
    header_base = struct.pack("<III", FRAMING_PHRASE, 12, calculate_crc(protocol_packet))
    assert packet == header_base + struct.pack("<I", calculate_crc(header_base)) + protocol_packet
    assert packet[:4] == b"\xfa\xb0\xfa\xb0"


def test_get_protocol_packet():
    request = req.PumpStartRequest()
    assert request.get_protocol_packet(b"xy") == struct.pack(
        "<IIHH", Command.PUMP_START.value, StatusCode.NOT_PROCESSED.value, 3, 1
    ) + b"xy"


def test_get_transport_packet():
    protocol_packet = b"\x01" * 13
    packet = req.Request().get_transport_packet(protocol_packet)
    framing, length, payload_crc, header_crc = struct.unpack("<IIII", packet[:16])
    assert framing == FRAMING_PHRASE
    assert length == 13
    assert payload_crc == calculate_crc(protocol_packet)
    assert header_crc == calculate_crc(packet[:12])
    assert packet[16:] == protocol_packet


@pytest.mark.parametrize(
    "payload", [b"", b"A", b"AB", b"ABC", b"ABCD", b"ABCDE", b"Test Echo Payload"]
)
def test_echo_payload_with_any_alignment(payload):
    request = req.EchoRequest(payload)
    assert request.get_payload() == payload
    command, _, _, sent_payload = parse_request(request.get_packet())
    assert command is Command.ECHO
    assert sent_payload == payload


def test_set_time_payload():
    request = req.SetTimeRequest(datetime(2026, 10, 27, 19, 27, 46, 999))
    assert request.get_payload() == b"2026-10-27T19:27:46\x00"
    _, _, _, payload = parse_request(request.get_packet())
    assert payload == b"2026-10-27T19:27:46\x00"


@pytest.mark.parametrize("setpoint", [0, 150, 0xFFFFFFFF])
def test_set_setpoint_payload(setpoint):
    request = req.SetSetpointRequest(setpoint)
    _, _, _, payload = parse_request(request.get_packet())
    assert payload == struct.pack("<I", setpoint)


def test_set_setpoint_negative_raises():
    with pytest.raises(struct.error):
        req.SetSetpointRequest(-1).get_packet()


def test_base_request_without_command_cannot_be_packed():
    with pytest.raises(struct.error):
        req.Request().get_packet()


def test_get_payload_without_codec_ignores_data():
    assert req.GetTimeRequest(data="ignored").get_payload() == b""
