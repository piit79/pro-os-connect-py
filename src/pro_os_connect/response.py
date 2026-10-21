import logging
import struct
from abc import ABC
from typing import Any, ClassVar

from .codecs import Codec
from .common import FRAMING_PHRASE, Command, Node, StatusCode
from .util import calculate_crc

_registry: dict[Command, type["Response"]] = {}


def response(cls: type["Response"]) -> type["Response"]:
    _registry[cls.COMMAND] = cls
    return cls


class ResponseError(Exception):
    description = "Response error"


class ResponseNotDefinedError(ResponseError):
    description = "Response not defined"


class ResponsePacketIncompleteError(ResponseError):
    description = "Response packet incomplete"


class ResponseFramingPhraseError(ResponseError):
    description = "Invalid framing phrase"


class ResponseHeaderCrcError(ResponseError):
    description = "Response header CRC error"


class ResponsePayloadLengthError(ResponseError):
    description = "Response payload length incorrect"


class ResponsePayloadCrcError(ResponseError):
    description = "Response payload CRC error"


class ResponsePacket:
    TRANSPORT_HEADER_SIZE = 16
    PROTOCOL_HEADER_SIZE = 12

    # List of commands with known incorrect CRC in the response
    KNOWN_INCORRECT_CRC_COMMANDS: ClassVar = [
        Command.GET_CPU_SERIAL_NUMBER,
        Command.GET_SERIAL_NUMBER,
        Command.GET_SOFTWARE_VERSION,
        Command.GET_NETWORK_CONFIGURATION,
    ]

    def __init__(self, data: bytes = b""):
        """
        :param data: Response network packet data
        :type data: bytes
        """
        self.data = data
        self.command: Command = Command.NONE
        self.status: StatusCode = StatusCode.NONE
        self.from_addr: Node | None = None
        self.to_addr: Node | None = None
        self.transport_header = b""
        self.protocol_packet = b""
        self.protocol_header = b""
        self.payload = b""
        self.logger = logging.getLogger(__name__)

    def append(self, data: bytes):
        """Add more packet data to the response"""
        self.data += data

    def __iadd__(self, data: bytes):
        self.append(data)
        return self

    def __len__(self) -> int:
        return len(self.data)

    def has_data(self) -> bool:
        return len(self) > 0

    def is_valid(self) -> bool:
        try:
            self.parse_transport_packet(test=True)
        except ResponseError:
            return False

        return True

    def parse_transport_packet(self, test=False):
        if len(self) < self.TRANSPORT_HEADER_SIZE:
            raise ResponsePacketIncompleteError()
        self.transport_header = self.data[: self.TRANSPORT_HEADER_SIZE]
        self.protocol_packet = self.data[self.TRANSPORT_HEADER_SIZE :]
        framing_phrase, payload_length, payload_crc, header_crc = struct.unpack(
            "<IIII", self.transport_header
        )
        if framing_phrase != FRAMING_PHRASE:
            msg = f"Response framing phrase invalid: {framing_phrase}"
            self.logger.error(msg)
            raise ResponseFramingPhraseError(msg)
        calculated_header_crc = calculate_crc(self.transport_header[:12])
        if calculated_header_crc != header_crc:
            msg = f"Response header CRC mismatch: calculated={calculated_header_crc} actual={header_crc}"
            self.logger.error(msg)
            raise ResponseHeaderCrcError(msg)
        if len(self.protocol_packet) != payload_length:
            msg = f"Response payload length mismatch: actual={len(self.protocol_packet)} header={payload_length}"
            level = logging.DEBUG if test else logging.ERROR
            self.logger.log(level, msg)
            raise ResponsePayloadLengthError(msg)
        calculated_payload_crc = calculate_crc(self.protocol_packet)
        if calculated_payload_crc != payload_crc:
            self.parse_protocol_packet()
            msg = f"Response payload CRC mismatch: calculated={calculated_payload_crc:x} received={payload_crc:x}"
            if self.command in self.KNOWN_INCORRECT_CRC_COMMANDS:
                self.logger.warning(msg)
            else:
                self.logger.error(msg)
                raise ResponsePayloadCrcError("Response payload CRC mismatch")

    def parse_protocol_packet(self):
        self.protocol_header = self.protocol_packet[: self.PROTOCOL_HEADER_SIZE]
        command, self.status, self.from_addr, self.to_addr = struct.unpack(
            "<IIHH", self.protocol_header
        )
        self.command = Command(command)
        self.payload = self.protocol_packet[self.PROTOCOL_HEADER_SIZE :]

    def parse(self):
        self.parse_transport_packet()
        self.parse_protocol_packet()

    def get_response(self) -> "Response | None":
        try:
            cls = _registry[self.command]
        except KeyError:
            raise ResponseError

        return cls(self)


class Response(ABC):
    COMMAND = Command.NONE
    CODEC: Codec | None = None

    def __init__(self, packet: ResponsePacket):
        """
        :param packet: Response network packet data
        :type packet: ResponsePacket
        """
        self.packet = packet
        self.status: StatusCode = self.packet.status
        self.from_addr = self.packet.from_addr
        self.to_addr = self.packet.to_addr
        self.result: Any = None
        self.unpack()

    def unpack(self):
        if self.CODEC is not None:
            self.result = self.CODEC.unpack(self.packet.payload)


@response
class GetProtocolVersionResponse(Response):
    COMMAND = Command.GET_PROTOCOL_VERSION
    CODEC = Codec.int("<I")


@response
class EchoResponse(Response):
    COMMAND = Command.ECHO
    CODEC = Codec.null()


#                           Response payload
# bit   0                         31 32                       63
#       +------+------+------+------+------+------+------+------+
#       |         Timestamp         |          Alarms           |
#       +------+------+------+------+------+------+------+------+
#       |          Setpoint         |         Flow Rate         |
#       +------+------+------+------+------+------+------+------+
#       |     Combined Pressure     |       Pre Pressure        |
#       +------+------+------+------+------+------+------+------+
#       |       Inlet Pressure      |  Pump Level | RCB  | RPB  |
#       +------+------+------+------+------+------+------+------+
#       | RIB  | CCB  | CPB  | Run  |
#       +------+------+------+------+
@response
class GetTelemetryResponse(Response):
    COMMAND = Command.GET_TELEMETRY
    CODEC = Codec.telemetry_fields()


@response
class GetTimeResponse(Response):
    COMMAND = Command.GET_TIME
    CODEC = Codec.iso_datetime()


@response
class SetTimeResponse(Response):
    COMMAND = Command.SET_TIME


@response
class PumpStartResponse(Response):
    COMMAND = Command.PUMP_START


@response
class PumpStopResponse(Response):
    COMMAND = Command.PUMP_STOP


@response
class GetSoftwareVersionResponse(Response):
    COMMAND = Command.GET_SOFTWARE_VERSION
    CODEC = Codec.cstr()


@response
class GetSerialNumberResponse(Response):
    COMMAND = Command.GET_SERIAL_NUMBER
    CODEC = Codec.cstr()


@response
class GetCpuSerialNumberResponse(Response):
    COMMAND = Command.GET_CPU_SERIAL_NUMBER
    CODEC = Codec.raw_hex()


@response
class GetNetworkConfigurationResponse(Response):
    COMMAND = Command.GET_NETWORK_CONFIGURATION
    CODEC = Codec.json_cstr()


@response
class SetSetpointResponse(Response):
    COMMAND = Command.SET_SETPOINT
