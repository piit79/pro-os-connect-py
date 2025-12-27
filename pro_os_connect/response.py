import struct
from .common import FRAMING_PHRASE, Command, Node, StatusCode
from .util import calculate_crc


class ResponseError(Exception):
    pass


class ResponsePacketIncompleteError(ResponseError):
    pass


class ResponseFramingPhraseError(ResponseError):
    pass


class ResponseHeaderCrcError(ResponseError):
    pass


class ResponsePayloadLengthError(ResponseError):
    pass


class ResponsePayloadCrcError(ResponseError):
    pass


class Response:
    TRANSPORT_HEADER_SIZE = 16
    PROTOCOL_HEADER_SIZE = 12

    KNOWN_WRONG_CRC_COMMANDS = [
        Command.GET_CPU_SERIAL_NUMBER.value,
        Command.GET_SERIAL_NUMBER.value,
        Command.GET_SOFTWARE_VERSION.value
    ]

    def __init__(self, data: bytes = b''):
        self.data = data
        self.command: Command | None = None
        self.status: StatusCode | None = None
        self.from_addr: Node | None = None
        self.to_addr: Node | None = None
        self.transport_header = b''
        self.protocol_packet = b''
        self.protocol_header = b''
        self.payload = b''

    def append(self, data: bytes):
        self.data += data

    def is_valid(self) -> bool:
        try:
            self.parse_transport_packet()
        except ResponseError as e:
            return False

        return True

    def parse_transport_packet(self):
        if len(self.data) < self.TRANSPORT_HEADER_SIZE:
            raise ResponsePacketIncompleteError()
        self.transport_header = self.data[:self.TRANSPORT_HEADER_SIZE]
        self.protocol_packet = self.data[self.TRANSPORT_HEADER_SIZE:]
        framing_phrase, payload_length, payload_crc, header_crc = struct.unpack("<IIII", self.transport_header)
        if framing_phrase != FRAMING_PHRASE:
            raise ResponseFramingPhraseError("Response framing phrase invalid")
        calculated_header_crc = calculate_crc(self.transport_header[:12])
        if calculated_header_crc != header_crc:
            raise ResponseHeaderCrcError("Response header CRC mismatch")
        if len(self.protocol_packet) != payload_length:
            raise ResponsePayloadLengthError(f"Response payload length mismatch: actual={len(self.protocol_packet)} header={payload_length}")
        calculated_payload_crc = calculate_crc(self.protocol_packet)
        if calculated_payload_crc != payload_crc:
            self.parse_protocol_packet()
            if self.command in self.KNOWN_WRONG_CRC_COMMANDS:
                print(f"Warning: Response payload CRC mismatch: calculated={calculated_payload_crc:x} received={payload_crc:x}")
            else:
                raise ResponsePayloadCrcError("Response payload CRC mismatch")

    def parse_protocol_packet(self):
        self.protocol_header = self.protocol_packet[:self.PROTOCOL_HEADER_SIZE]
        self.command, self.status, self.from_addr, self.to_addr = struct.unpack("<IIHH", self.protocol_header)
        self.payload = self.protocol_packet[self.PROTOCOL_HEADER_SIZE:]

    def unpack_payload(self):
        pass

    def parse(self):
        self.parse_transport_packet()
        self.parse_protocol_packet()

    def unpack(self, format) -> tuple:
        return struct.unpack(format, self.payload)
