import struct
from .common import FRAMING_PHRASE, Command, Node, StatusCode
from .util import calculate_crc


class Request:
    def __init__(self, command: Command, payload: bytes = b'', to_addr: Node = Node.PRO_OS):
        self.command = command
        self.payload = payload
        self.from_addr = Node.CLIENT
        self.to_addr = to_addr

    # bit   0        31 32      63 64 79 80 95 96
    #       +----------+----------+-----+-----+----------
    #       | Command  | Status   | From| To  | Data
    #       | Code     | Code     |     |     | Bytes
    #       +----------+----------+-----+-----+----------
    #       Protocol Header                   | Payload -->
    def get_protocol_packet(self, payload: bytes) -> bytes:
        """Create and return the protocol packet from the command payload"""
        protocol_packet = struct.pack("<IIHH",
                                        self.command.value,
                                        StatusCode.NOT_PROCESSED.value,
                                        self.from_addr.value,
                                        self.to_addr.value)
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
        header_base = struct.pack("<III", FRAMING_PHRASE, len(protocol_packet), payload_crc)
        header_crc = calculate_crc(header_base)
        header = header_base + struct.pack("<I", header_crc)
        return header + protocol_packet

    def get_packet(self) -> bytes:
        """Create and return the network packet data"""
        protocol_packet = self.get_protocol_packet(self.payload)
        transport_packet = self.get_transport_packet(protocol_packet)
        return transport_packet
