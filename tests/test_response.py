import logging
import struct
from datetime import datetime

import pytest

from pro_os_connect import response as resp
from pro_os_connect.common import Command, Node, StatusCode, TelemetryFields
from pro_os_connect.response import (
    Response,
    ResponseCommandNotImplementedError,
    ResponseError,
    ResponseFramingPhraseError,
    ResponseHeaderCrcError,
    ResponsePacket,
    ResponsePacketIncompleteError,
    ResponsePayloadCrcError,
    ResponsePayloadLengthError,
)

from .helpers import CAPTURED, build_packet, captured

ERRORS = [
    resp.ResponseNotDefinedError,
    ResponsePacketIncompleteError,
    ResponseFramingPhraseError,
    ResponseHeaderCrcError,
    ResponsePayloadLengthError,
    ResponsePayloadCrcError,
    ResponseCommandNotImplementedError,
]


@pytest.mark.parametrize("error", ERRORS, ids=lambda e: e.__name__)
def test_errors_are_response_errors_with_description(error):
    assert issubclass(error, ResponseError)
    assert error.description
    assert error.description != ResponseError.description


class TestResponsePacketContainer:
    def test_empty(self):
        packet = ResponsePacket()
        assert len(packet) == 0
        assert not packet.has_data()
        assert packet.command is Command.NONE
        assert packet.status is StatusCode.NONE
        assert packet.from_addr is None
        assert packet.to_addr is None
        assert packet.payload == b""

    def test_append(self):
        packet = ResponsePacket(b"ab")
        packet.append(b"cd")
        assert packet.data == b"abcd"
        assert len(packet) == 4
        assert packet.has_data()

    def test_iadd(self):
        packet = ResponsePacket()
        original = packet
        packet += b"xyz"
        assert packet is original
        assert packet.data == b"xyz"


class TestParseValid:
    def test_parse_fields(self):
        packet = ResponsePacket(build_packet(Command.GET_TIME, b"payload!"))
        packet.parse()
        assert packet.command is Command.GET_TIME
        assert packet.status is StatusCode.OK
        assert packet.from_addr is Node.PRO_OS
        assert packet.to_addr is Node.CLIENT
        assert packet.payload == b"payload!"
        assert len(packet.transport_header) == 16
        assert len(packet.protocol_header) == 12
        assert packet.protocol_packet == packet.protocol_header + packet.payload

    def test_parse_from_network_card(self):
        packet = ResponsePacket(build_packet(Command.ECHO, from_addr=Node.NETWORK_CARD))
        packet.parse()
        assert packet.from_addr is Node.NETWORK_CARD

    @pytest.mark.parametrize("status", list(StatusCode)[1:], ids=lambda s: s.name)
    def test_parse_any_known_status(self, status):
        packet = ResponsePacket(build_packet(Command.PUMP_START, status=status))
        packet.parse()
        assert packet.status is status

    @pytest.mark.parametrize("length", range(9))
    def test_payload_of_any_alignment(self, length):
        payload = bytes(range(length))
        packet = ResponsePacket(build_packet(Command.ECHO, payload))
        assert packet.is_valid()
        packet.parse()
        assert packet.payload == payload

    @pytest.mark.parametrize("command", list(CAPTURED), ids=lambda c: c.name)
    def test_real_device_packets_are_valid(self, command, caplog):
        packet = ResponsePacket(captured(command))
        with caplog.at_level(logging.DEBUG):
            assert packet.is_valid()
            packet.parse()
        assert caplog.records == []
        assert packet.command is command
        assert packet.status is StatusCode.OK
        assert (packet.from_addr, packet.to_addr) == (Node.PRO_OS, Node.CLIENT)

    def test_is_valid_does_not_parse_protocol_packet(self):
        packet = ResponsePacket(build_packet(Command.GET_TIME))
        assert packet.is_valid()
        assert packet.command is Command.NONE


class TestParseInvalid:
    def test_empty_is_incomplete(self):
        with pytest.raises(ResponsePacketIncompleteError):
            ResponsePacket().parse()

    @pytest.mark.parametrize("length", [1, 8, 15])
    def test_partial_header_is_incomplete(self, length):
        packet = ResponsePacket(build_packet(Command.GET_TIME)[:length])
        assert not packet.is_valid()
        with pytest.raises(ResponsePacketIncompleteError):
            packet.parse()

    def test_bad_framing_phrase(self, caplog):
        packet = ResponsePacket(build_packet(Command.GET_TIME, framing_phrase=0xDEADBEEF))
        assert not packet.is_valid()
        with pytest.raises(ResponseFramingPhraseError):
            packet.parse()
        assert "framing phrase" in caplog.text

    def test_bad_header_crc(self):
        packet = ResponsePacket(build_packet(Command.GET_TIME, header_crc=0))
        assert not packet.is_valid()
        with pytest.raises(ResponseHeaderCrcError):
            packet.parse()

    def test_corrupted_header_byte(self):
        data = bytearray(build_packet(Command.GET_TIME, b"abcd"))
        data[5] ^= 0x01
        with pytest.raises(ResponseHeaderCrcError):
            ResponsePacket(bytes(data)).parse()

    def test_truncated_payload(self):
        packet = ResponsePacket(build_packet(Command.GET_TIME, b"abcdef")[:-1])
        assert not packet.is_valid()
        with pytest.raises(ResponsePayloadLengthError):
            packet.parse()

    def test_trailing_extra_data(self):
        packet = ResponsePacket(build_packet(Command.GET_TIME, b"abcd") + b"\x00")
        with pytest.raises(ResponsePayloadLengthError):
            packet.parse()

    def test_two_concatenated_packets_are_rejected(self):
        data = build_packet(Command.GET_TIME) * 2
        with pytest.raises(ResponsePayloadLengthError):
            ResponsePacket(data).parse()

    def test_length_mismatch_logged_at_debug_while_testing(self, caplog):
        packet = ResponsePacket(build_packet(Command.GET_TIME, b"abcdef")[:-1])
        with caplog.at_level(logging.DEBUG):
            packet.is_valid()
        assert [r.levelno for r in caplog.records] == [logging.DEBUG]

    def test_length_mismatch_logged_at_error_when_parsing(self, caplog):
        packet = ResponsePacket(build_packet(Command.GET_TIME, b"abcdef")[:-1])
        with pytest.raises(ResponsePayloadLengthError):
            packet.parse()
        assert [r.levelno for r in caplog.records] == [logging.ERROR]

    def test_bad_payload_crc(self, caplog):
        packet = ResponsePacket(build_packet(Command.GET_TIME, b"abcd", payload_crc=0))
        assert not packet.is_valid()
        with pytest.raises(ResponsePayloadCrcError):
            packet.parse()
        assert "payload CRC mismatch" in caplog.text

    @pytest.mark.parametrize("command", list(CAPTURED), ids=lambda c: c.name)
    def test_corrupted_payload_of_real_packets(self, command):
        data = bytearray(captured(command))
        data[-1] ^= 0xFF
        with pytest.raises(ResponsePayloadCrcError):
            ResponsePacket(bytes(data)).parse()

    def test_unpadded_crc_rejected_for_unaligned_payload(self):
        from crccheck.crc import Crc32Mpeg2

        protocol_packet = build_packet(Command.ECHO, b"ABCDE")[16:]
        data = build_packet(Command.ECHO, b"ABCDE", payload_crc=Crc32Mpeg2.calc(protocol_packet))
        with pytest.raises(ResponsePayloadCrcError):
            ResponsePacket(data).parse()

    def test_unknown_command(self):
        packet = ResponsePacket(build_packet(0x0001FFFF))
        with pytest.raises(ValueError):
            packet.parse()

    @pytest.mark.xfail(
        raises=ValueError,
        reason="Unknown status codes raise a bare ValueError instead of a ResponseError",
    )
    def test_unknown_status_is_response_error(self):
        packet = ResponsePacket(build_packet(Command.GET_TIME, status=0x12345678))
        with pytest.raises(ResponseError):
            packet.parse()


RESPONSE_CLASSES = [
    (resp.GetProtocolVersionResponse, Command.GET_PROTOCOL_VERSION),
    (resp.EchoResponse, Command.ECHO),
    (resp.RebootResponse, Command.REBOOT),
    (resp.GetTelemetryResponse, Command.GET_TELEMETRY),
    (resp.GetTimeResponse, Command.GET_TIME),
    (resp.SetTimeResponse, Command.SET_TIME),
    (resp.PumpStartResponse, Command.PUMP_START),
    (resp.PumpStopResponse, Command.PUMP_STOP),
    (resp.GetSoftwareVersionResponse, Command.GET_SOFTWARE_VERSION),
    (resp.GetSerialNumberResponse, Command.GET_SERIAL_NUMBER),
    (resp.GetCpuSerialNumberResponse, Command.GET_CPU_SERIAL_NUMBER),
    (resp.GetNetworkConfigurationResponse, Command.GET_NETWORK_CONFIGURATION),
    (resp.SetSetpointResponse, Command.SET_SETPOINT),
]


class TestResponse:
    def test_every_registered_response_is_tested(self):
        assert set(resp._registry) == {command for _, command in RESPONSE_CLASSES}

    @pytest.mark.parametrize(("cls", "command"), RESPONSE_CLASSES, ids=lambda x: getattr(x, "__name__", ""))
    def test_registry(self, cls, command):
        assert cls.COMMAND is command
        assert resp._registry[command] is cls

    @pytest.mark.parametrize(
        "command",
        [Command.SOFTWARE_UPDATE_START, Command.SOFTWARE_UPDATE_FINISH, Command.FILE_GET_PAGE, Command.SET_CLIENT_TIMEOUT],
        ids=lambda c: c.name,
    )
    def test_unregistered_command(self, command):
        assert command not in resp._registry
        packet = ResponsePacket(build_packet(command))
        packet.parse()
        with pytest.raises(ResponseCommandNotImplementedError, match=command.name):
            packet.get_response()

    def test_attributes_copied_from_packet(self):
        packet = ResponsePacket(
            build_packet(Command.PUMP_STOP, status=StatusCode.WRITE_PROTECTED, from_addr=Node.NETWORK_CARD)
        )
        packet.parse()
        response = packet.get_response()
        assert response.packet is packet
        assert response.status is StatusCode.WRITE_PROTECTED
        assert response.from_addr is Node.NETWORK_CARD
        assert response.to_addr is Node.CLIENT

    @pytest.mark.parametrize(
        "command", [Command.REBOOT, Command.SET_TIME, Command.PUMP_START, Command.PUMP_STOP, Command.SET_SETPOINT]
    )
    def test_responses_without_codec_have_no_result(self, command):
        packet = ResponsePacket(build_packet(command, b"ignored"))
        packet.parse()
        assert packet.get_response().result is None

    def test_echo_result_is_raw_payload(self):
        packet = ResponsePacket(build_packet(Command.ECHO, b"Test Echo Payload"))
        packet.parse()
        assert packet.get_response().result == b"Test Echo Payload"

    def test_from_packet_on_base_class(self):
        packet = ResponsePacket(build_packet(Command.GET_PROTOCOL_VERSION, struct.pack("<I", 7)))
        packet.parse()
        response = Response.from_packet(packet)
        assert isinstance(response, resp.GetProtocolVersionResponse)
        assert response.result == 7

    def test_codec_error_propagates(self):
        packet = ResponsePacket(build_packet(Command.GET_PROTOCOL_VERSION, b"\x01"))
        packet.parse()
        with pytest.raises(struct.error):
            packet.get_response()


class TestRealDeviceResults:
    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            (Command.GET_PROTOCOL_VERSION, 1),
            (Command.GET_TIME, datetime(2026, 10, 27, 19, 27, 46)),
            (Command.GET_SOFTWARE_VERSION, "v2.0.0"),
            (Command.GET_SERIAL_NUMBER, "2511000618"),
            (Command.GET_CPU_SERIAL_NUMBER, "5767072d39314637424e79137d2e5454"),
            (Command.GET_NETWORK_CONFIGURATION, {"manual": {}, "write_enabled": True}),
        ],
        ids=lambda x: getattr(x, "name", ""),
    )
    def test_result(self, command, expected):
        packet = ResponsePacket(captured(command))
        packet.parse()
        assert packet.get_response().result == expected

    def test_telemetry(self):
        packet = ResponsePacket(captured(Command.GET_TELEMETRY))
        packet.parse()
        result = packet.get_response().result
        assert isinstance(result, TelemetryFields)
        assert result.timestamp == 10362745
        assert result.alarms == 0
        assert result.setpoint == 150
        assert result.flow_rate == pytest.approx(4.9571, abs=1e-4)
        assert result.combined_pressure == pytest.approx(19.6900, abs=1e-4)
        assert result.pre_pressure == pytest.approx(17.5108, abs=1e-4)
        assert result.inlet_pressure == pytest.approx(18.4419, abs=1e-4)
        assert result.pump_level == 0
        assert result.is_running == 0
