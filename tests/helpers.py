import socket
import struct
import threading
from collections.abc import Callable

from pro_os_connect.common import FRAMING_PHRASE, Command, Node, StatusCode
from pro_os_connect.util import calculate_crc

# Raw responses captured from a real device (firmware v2.0.0)
CAPTURED = {
    Command.GET_PROTOCOL_VERSION: "fab0fab010000000c602d9979e7132b701000100000000000100030001000000",
    Command.GET_TELEMETRY: (
        "fab0fab03000000006b1d760963176af040001000000000001000300791f9e00000000009600"
        "00004ba09e401d859d411a168c41238993410000000000000000"
    ),
    Command.GET_TIME: (
        "fab0fab020000000992d03a7a4733d9b060001000000000001000300323032362d31302d3237"
        "5431393a32373a343600"
    ),
    Command.GET_SOFTWARE_VERSION: (
        "fab0fab01300000072bb2d8b79a5eb9611000100000000000100030076322e302e3000"
    ),
    Command.GET_SERIAL_NUMBER: (
        "fab0fab01700000043fb9fb2fe6d61af1200010000000000010003003235313130303036313800"
    ),
    Command.GET_CPU_SERIAL_NUMBER: (
        "fab0fab01c0000006b6334ce119021f41300010000000000010003005767072d39314637424e"
        "79137d2e5454"
    ),
    Command.GET_NETWORK_CONFIGURATION: (
        "fab0fab03e0000007c907142869074c31400010000000000010003007b0a20202020226d616e"
        "75616c223a207b7d2c0a202020202277726974655f656e61626c6564223a20747275650a2020"
        "7d00"
    ),
}


def captured(command: Command) -> bytes:
    return bytes.fromhex(CAPTURED[command])


def build_packet(
    command: Command | int,
    payload: bytes = b"",
    status: StatusCode | int = StatusCode.OK,
    from_addr: Node | int = Node.PRO_OS,
    to_addr: Node | int = Node.CLIENT,
    framing_phrase: int = FRAMING_PHRASE,
    payload_length: int | None = None,
    payload_crc: int | None = None,
    header_crc: int | None = None,
) -> bytes:
    """Build a transport packet, optionally overriding any header field to make it invalid"""
    command = command.value if isinstance(command, Command) else command
    status = status.value if isinstance(status, StatusCode) else status
    from_addr = from_addr.value if isinstance(from_addr, Node) else from_addr
    to_addr = to_addr.value if isinstance(to_addr, Node) else to_addr
    protocol_packet = (
        struct.pack("<IIHH", command, status, from_addr, to_addr) + payload
    )
    if payload_length is None:
        payload_length = len(protocol_packet)
    if payload_crc is None:
        payload_crc = calculate_crc(protocol_packet)
    header_base = struct.pack("<III", framing_phrase, payload_length, payload_crc)
    if header_crc is None:
        header_crc = calculate_crc(header_base)
    return header_base + struct.pack("<I", header_crc) + protocol_packet


def parse_request(data: bytes) -> tuple[Command, Node, Node, bytes]:
    """Validate a request packet like the device does and return its fields"""
    framing_phrase, length, payload_crc, header_crc = struct.unpack("<IIII", data[:16])
    assert framing_phrase == FRAMING_PHRASE
    assert header_crc == calculate_crc(data[:12])
    protocol_packet = data[16:]
    assert len(protocol_packet) == length
    assert payload_crc == calculate_crc(protocol_packet)
    command, status, from_addr, to_addr = struct.unpack("<IIHH", protocol_packet[:12])
    assert status == StatusCode.NOT_PROCESSED.value
    return Command(command), Node(from_addr), Node(to_addr), protocol_packet[12:]


# A handler gets the raw request and returns the response chunks to send, each sent
# separately. Returning None closes the connection, an empty list sends nothing.
Handler = Callable[[bytes], list[bytes] | None]


class FakeDevice:
    """TCP server on localhost standing in for a Pro OS Connect device"""

    def __init__(self):
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        self.handler: Handler = lambda request: []
        self.requests: list[bytes] = []
        self.connections = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        self.server.settimeout(0.05)
        while not self._stop.is_set():
            try:
                conn, _ = self.server.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            self.connections += 1
            with conn:
                self._handle(conn)

    def _handle(self, conn: socket.socket):
        conn.settimeout(0.05)
        buffer = b""
        while not self._stop.is_set():
            try:
                data = conn.recv(4096)
            except TimeoutError:
                continue
            except OSError:
                return
            if not data:
                return
            buffer += data
            # Wait for a whole request: transport header plus the payload length it declares
            while len(buffer) >= 16:
                size = 16 + struct.unpack("<I", buffer[4:8])[0]
                if len(buffer) < size:
                    break
                request, buffer = buffer[:size], buffer[size:]
                self.requests.append(request)
                chunks = self.handler(request)
                if chunks is None:
                    return
                try:
                    for chunk in chunks:
                        conn.sendall(chunk)
                except OSError:
                    return

    def respond(self, *chunks: bytes):
        """Always respond with the given chunks"""
        self.handler = lambda request: list(chunks)

    def respond_ok(self, payload: bytes = b""):
        """Respond with a valid OK packet for whatever command was requested"""

        def handler(request: bytes) -> list[bytes]:
            command, _, _, _ = parse_request(request)
            return [build_packet(command, payload)]

        self.handler = handler

    def close(self):
        self._stop.set()
        self.server.close()
        self._thread.join(timeout=1)


TELEMETRY_FORMAT = "<IIIffffHBBBBBb"


class SimulatedDevice:
    """Stateful handler that answers every supported command like a real device"""

    def __init__(self):
        self.setpoint = 150
        self.running = False
        self.time = "2026-10-27T19:27:46"
        self.status = StatusCode.OK

    def telemetry(self) -> bytes:
        return struct.pack(
            TELEMETRY_FORMAT,
            10362745, 0, self.setpoint, 4.95712, 19.69, 17.51076, 18.44193,
            300 if self.running else 0, 1, 2, 3, 4, 5, int(self.running),
        )

    def __call__(self, request: bytes) -> list[bytes]:
        command, _, _, payload = parse_request(request)
        if self.status != StatusCode.OK:
            return [build_packet(command, status=self.status)]
        match command:
            case Command.GET_PROTOCOL_VERSION:
                result = struct.pack("<I", 1)
            case Command.ECHO:
                result = payload
            case Command.GET_TELEMETRY:
                result = self.telemetry()
            case Command.GET_TIME:
                result = self.time.encode() + b"\x00"
            case Command.SET_TIME:
                self.time = payload.rstrip(b"\x00").decode()
                result = b""
            case Command.PUMP_START | Command.PUMP_STOP:
                self.running = command is Command.PUMP_START
                result = b""
            case Command.GET_SOFTWARE_VERSION:
                result = b"v2.0.0\x00"
            case Command.GET_SERIAL_NUMBER:
                result = b"2511000618\x00"
            case Command.GET_CPU_SERIAL_NUMBER:
                result = bytes.fromhex("5767072d39314637424e79137d2e5454")
            case Command.GET_NETWORK_CONFIGURATION:
                result = b'{\n    "manual": {},\n    "write_enabled": true\n  }\x00'
            case Command.SET_SETPOINT:
                (self.setpoint,) = struct.unpack("<I", payload)
                result = b""
            case _:
                return [build_packet(command, status=StatusCode.INVALID_COMMAND)]
        return [build_packet(command, result)]
