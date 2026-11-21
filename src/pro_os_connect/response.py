import logging
import struct
from collections.abc import Mapping
from datetime import datetime
from json import JSONDecodeError
from typing import Any, Generic, NoReturn, TypeVar

from .codecs import Codec
from .common import FRAMING_PHRASE, Command, Node, StatusCode, TelemetryFields
from .util import calculate_crc

T = TypeVar("T")
R = TypeVar("R", bound="Response[Any]")

_registry: dict[Command, type["Response"]] = {}


def response(cls: type[R]) -> type[R]:
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


class ResponseCommandNotImplementedError(ResponseError):
    description = "Response command not implemented"


class ResponseInternalError(ResponseError):
    description = "Response internal error"


class ResponseDecodeError(ResponseError):
    description = "Response decode error"


class ResponsePacket:
    TRANSPORT_HEADER_SIZE = 16
    PROTOCOL_HEADER_SIZE = 12

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
            self.parse_transport_packet()
        except ResponseError:
            return False

        return True

    def parse_transport_packet(self):
        """Validate the transport header and payload and split the packet.

        Errors are only logged at DEBUG: the raised exception carries the message, so callers that handle it don't
        get an error in their log for every corrupted packet.
        """
        if len(self) < self.TRANSPORT_HEADER_SIZE:
            raise ResponsePacketIncompleteError()
        self.transport_header = self.data[: self.TRANSPORT_HEADER_SIZE]
        self.protocol_packet = self.data[self.TRANSPORT_HEADER_SIZE :]
        framing_phrase, payload_length, payload_crc, header_crc = struct.unpack("<IIII", self.transport_header)
        if framing_phrase != FRAMING_PHRASE:
            self._fail(ResponseFramingPhraseError(f"0x{framing_phrase:x}"))
        calculated_header_crc = calculate_crc(self.transport_header[:12])
        if calculated_header_crc != header_crc:
            self._fail(ResponseHeaderCrcError(f"received=0x{header_crc:x} calculated=0x{calculated_header_crc:x}"))
        if len(self.protocol_packet) != payload_length:
            self._fail(ResponsePayloadLengthError(f"header={payload_length} actual={len(self.protocol_packet)}"))
        calculated_payload_crc = calculate_crc(self.protocol_packet)
        if calculated_payload_crc != payload_crc:
            self._fail(ResponsePayloadCrcError(f"received=0x{payload_crc:x} calculated=0x{calculated_payload_crc:x}"))

    def _fail(self, error: ResponseError) -> NoReturn:
        self.logger.debug("%s: %s", type(error).__name__, error)
        raise error

    def parse_protocol_packet(self):
        self.protocol_header = self.protocol_packet[: self.PROTOCOL_HEADER_SIZE]
        command, status, from_addr, to_addr = struct.unpack("<IIHH", self.protocol_header)
        try:
            self.command = Command(command)
            self.status = StatusCode(status)
            self.from_addr = Node(from_addr)
            self.to_addr = Node(to_addr)
        except ValueError as e:
            raise ResponseInternalError(f"Invalid value: {e}") from None
        self.payload = self.protocol_packet[self.PROTOCOL_HEADER_SIZE :]

    def parse(self):
        self.parse_transport_packet()
        self.parse_protocol_packet()

    def get_response(self) -> "Response":
        try:
            return Response.from_packet(self)
        except (struct.error, UnicodeDecodeError, JSONDecodeError, ValueError) as e:
            raise ResponseDecodeError(f"Error decoding response: {e}") from e


class Response(Generic[T]):
    """Base class for responses. Subclasses must set COMMAND."""

    COMMAND = Command.NONE
    CODEC: Codec[T]
    result: T

    def __init__(self, packet: ResponsePacket):
        """
        :param packet: Response network packet data
        :type packet: ResponsePacket
        """
        if self.COMMAND is Command.NONE:
            raise TypeError(f"{type(self).__name__} must define COMMAND")
        if not hasattr(self, "CODEC"):
            raise TypeError(f"{type(self).__name__} must define CODEC")
        self.packet = packet
        self.status: StatusCode = self.packet.status
        self.from_addr = self.packet.from_addr
        self.to_addr = self.packet.to_addr
        self.unpack()

    def unpack(self):
        self.result = self.CODEC.unpack(self.packet.payload)

    @classmethod
    def from_packet(cls, packet: ResponsePacket) -> "Response":
        try:
            resp_cls = _registry[packet.command]
        except KeyError:
            raise ResponseCommandNotImplementedError(
                f"Response command {packet.command.name} not implemented"
            ) from None

        return resp_cls(packet)


@response
class GetProtocolVersionResponse(Response[int]):
    COMMAND = Command.GET_PROTOCOL_VERSION
    CODEC = Codec.int("<I")


@response
class EchoResponse(Response[bytes]):
    COMMAND = Command.ECHO
    CODEC = Codec.raw()


@response
class RebootResponse(Response[None]):
    COMMAND = Command.REBOOT
    CODEC = Codec.empty()


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
class GetTelemetryResponse(Response[TelemetryFields]):
    COMMAND = Command.GET_TELEMETRY
    CODEC = Codec.telemetry_fields()


@response
class GetTimeResponse(Response[datetime]):
    COMMAND = Command.GET_TIME
    CODEC = Codec.iso_datetime()


@response
class SetTimeResponse(Response[None]):
    COMMAND = Command.SET_TIME
    CODEC = Codec.empty()


@response
class PumpStartResponse(Response[None]):
    COMMAND = Command.PUMP_START
    CODEC = Codec.empty()


@response
class PumpStopResponse(Response[None]):
    COMMAND = Command.PUMP_STOP
    CODEC = Codec.empty()


@response
class GetSoftwareVersionResponse(Response[str]):
    COMMAND = Command.GET_SOFTWARE_VERSION
    CODEC = Codec.cstr()


@response
class GetSerialNumberResponse(Response[str]):
    COMMAND = Command.GET_SERIAL_NUMBER
    CODEC = Codec.cstr()


@response
class GetCpuSerialNumberResponse(Response[str]):
    COMMAND = Command.GET_CPU_SERIAL_NUMBER
    CODEC = Codec.raw_hex()


@response
class GetNetworkConfigurationResponse(Response[Mapping]):
    COMMAND = Command.GET_NETWORK_CONFIGURATION
    CODEC = Codec.json_cstr()


@response
class SetSetpointResponse(Response[None]):
    COMMAND = Command.SET_SETPOINT
    CODEC = Codec.empty()
