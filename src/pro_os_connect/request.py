import logging
import struct
from abc import ABC
from typing import Any

from .codecs import Codec
from .common import FRAMING_PHRASE, Command, Node, StatusCode
from .util import calculate_crc


class Request(ABC):
    COMMAND = Command.NONE
    CODEC: Codec | None = None

    def __init__(self, data: Any = None, to_addr: Node = Node.PRO_OS):
        """
        :param data: Command data
        :type data: Any
        :param to_addr: Destination address - Node.PRO_OS or Node.NETWORK_CARD
        :type to_addr: Node
        """
        self.data = data
        self.from_addr = Node.CLIENT
        self.to_addr = to_addr
        self.logger = logging.getLogger(__name__)

    # bit   0        31 32      63 64 79 80 95 96
    #       +----------+----------+-----+-----+----------
    #       | Command  | Status   | From| To  | Data
    #       | Code     | Code     |     |     | Bytes
    #       +----------+----------+-----+-----+----------
    #       Protocol Header                   | Payload -->
    def get_protocol_packet(self, payload: bytes) -> bytes:
        """Create and return the protocol packet from the command payload"""
        protocol_packet = struct.pack(
            "<IIHH",
            self.COMMAND.value,
            StatusCode.NOT_PROCESSED.value,
            self.from_addr.value,
            self.to_addr.value,
        )
        return protocol_packet + payload

    # bit   0        31 32      63 64      95 96     127
    #       +----------+----------+----------+----------+----------
    #       | Framing  | Payload  | Payload  | Header   | Data
    #       | phrase   | Length   | CRC      | CRC      | Bytes
    #       +----------+----------+----------+----------+----------
    #       Transport Header                            | Payload -->
    def get_transport_packet(self, protocol_packet: bytes) -> bytes:
        """Create and return the transport packet from the protocol packet"""
        payload_crc = calculate_crc(protocol_packet)
        header_base = struct.pack(
            "<III", FRAMING_PHRASE, len(protocol_packet), payload_crc
        )
        header_crc = calculate_crc(header_base)
        header = header_base + struct.pack("<I", header_crc)
        return header + protocol_packet

    def get_payload(self) -> bytes:
        """Return the encoded command data"""
        if self.CODEC is not None:
            return self.CODEC.pack(self.data)
        return b""

    def get_packet(self) -> bytes:
        """Create and return the network packet data"""
        payload = self.get_payload()
        protocol_packet = self.get_protocol_packet(payload)
        transport_packet = self.get_transport_packet(protocol_packet)
        return transport_packet


class GetProtocolVersionRequest(Request):
    COMMAND = Command.GET_PROTOCOL_VERSION


class EchoRequest(Request):
    COMMAND = Command.ECHO
    CODEC = Codec.null()


class RebootRequest(Request):
    COMMAND = Command.REBOOT


class GetTelemetryRequest(Request):
    COMMAND = Command.GET_TELEMETRY


class GetTimeRequest(Request):
    COMMAND = Command.GET_TIME


class SetTimeRequest(Request):
    COMMAND = Command.SET_TIME
    CODEC = Codec.iso_datetime()


class PumpStartRequest(Request):
    COMMAND = Command.PUMP_START


class PumpStopRequest(Request):
    COMMAND = Command.PUMP_STOP


class GetSoftwareVersionRequest(Request):
    COMMAND = Command.GET_SOFTWARE_VERSION


class GetSerialNumberRequest(Request):
    COMMAND = Command.GET_SERIAL_NUMBER


class GetCpuSerialNumberRequest(Request):
    COMMAND = Command.GET_CPU_SERIAL_NUMBER


class GetNetworkConfigurationRequest(Request):
    COMMAND = Command.GET_NETWORK_CONFIGURATION


class SetSetpointRequest(Request):
    COMMAND = Command.SET_SETPOINT
    CODEC = Codec.int("<I")
