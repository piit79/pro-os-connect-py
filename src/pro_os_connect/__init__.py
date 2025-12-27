from collections import namedtuple
from datetime import datetime
import logging
import select
import socket
import struct
import time
from .request import Command, Request
from .response import Response, ResponseError, StatusCode


TCP_PORT = 2345


class ProOSConnectError(Exception):
    pass


class ProOSConnectConnectError(ProOSConnectError):
    pass


class ProOSConnectTimeoutError(ProOSConnectError):
    pass


class ProOSConnectResponseError(ProOSConnectError):
    pass


TelemetryFields = namedtuple('TelemetryFields', [
    'timestamp',
    'alarms',
    'setpoint',
    'flow_rate',
    'combined_pressure',
    'pre_pressure',
    'inlet_pressure',
    'pump_level',
    'rcb',
    'rpb',
    'rib',
    'ccb',
    'cpb',
    'is_running',
])


class ProOSConnect:
    def __init__(self, host: str, port: int = TCP_PORT, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.socket = None
        self.running = False
        self.response: Response | None = None
        self.logger = logging.getLogger('ProOSConnect')

    def connect(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.socket.connect((self.host, self.port))
            self.socket.setblocking(False)
        except (ConnectionRefusedError, socket.gaierror) as e:
            raise ProOSConnectConnectError(e)


    def disconnect(self):
        if self.socket:
            self.socket.close()
            self.socket = None

    def get_response(self) -> Response:
        assert self.socket is not None
        readers = [self.socket]
        select_timeout = 0.1  # seconds
        response = Response()
        start_time = time.time()
        while not response.is_valid() and (time.time() - start_time) < self.timeout:
            read_ready, _, _ = select.select(readers, [], [], select_timeout)
            if not read_ready:
                continue

            packet = self.socket.recv(4096)
            self.logger.debug(f"Received packet: {packet.hex()}")
            print(f"Received packet: {packet.hex()}")
            if packet:
                response.append(packet)

        return response

    def send_request(self, request: Request) -> Response | None:
        if not self.socket:
            self.connect()

        packet = request.get_packet()
        self.logger.debug(f"Sending packet: {packet.hex()}")
        self.socket.sendall(packet) # type: ignore

        self.logger.debug("Waiting for response...")
        response = self.get_response()

        if not response.is_valid():
            raise ProOSConnectTimeoutError("Timeout waiting for response")

        response.parse()

        if response.status != StatusCode.OK.value:
            self.logger.debug(f"Response status: {response.status}")
            raise ProOSConnectResponseError(f"Error in response: {StatusCode(response.status).name}")

        return response

    def do_request(self, request: Request) -> Response | None:
        response = None
        try:
            response = self.send_request(request)
        except ProOSConnectError as e:
            self.logger.error(f"Error sending request: {e}")
            return None
        except ResponseError as e:
            self.logger.warning(f"Error parsing response: {e}")

        return response

    def get_protocol_version(self) -> int | None:
        request = Request(Command.GET_PROTOCOL_VERSION)
        response = self.do_request(request)
        if not response or len(response.payload) < 4:
            return None

        protocol_version, = struct.unpack("<I", response.payload[:4])
        return protocol_version

    # FIXME: Doesn't work for some reason? No response received
    def echo(self, test_payload: bytes | None = None) -> bool:
        if test_payload is None:
            test_payload = b'Test Echo Payload'
        request = Request(Command.ECHO, payload=test_payload)
        response = self.do_request(request)
        if not response or len(response.payload) < len(test_payload):
            return False

        return response.payload == test_payload

    def reboot(self) -> bool:
        request = Request(Command.REBOOT)
        response = self.do_request(request)
        return response is not None

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
        #                           Response payload
    def get_telemetry(self) -> TelemetryFields | None:
        request = Request(Command.GET_TELEMETRY)
        response = self.do_request(request)
        if not response:
            return None

        fields = TelemetryFields(*response.unpack("<IIIIIIIHBBBBBb"))
        return fields

    def get_uptime(self) -> int | None:
        """Return device uptime in seconds."""
        fields = self.get_telemetry()
        if fields:
            return int(fields.timestamp)
        return None

    def is_running(self) -> bool | None:
        fields = self.get_telemetry()
        if fields:
            return bool(fields.is_running)
        return None

    def get_time(self) -> datetime | None:
        request = Request(Command.GET_TIME)
        response = self.do_request(request)
        assert response is not None
        time_str = response.payload.decode('utf-8').strip('\x00')
        date_obj = datetime.fromisoformat(time_str)
        return date_obj

    def set_time(self, date_obj: datetime) -> bool:
        time_str = date_obj.isoformat()
        payload = time_str.encode('utf-8') + b'\x00'
        request = Request(Command.SET_TIME, payload=payload)
        response = self.do_request(request)
        return response is not None

    def pump_start(self) -> bool:
        request = Request(Command.PUMP_START)
        response = self.do_request(request)
        return response is not None

    def pump_stop(self) -> bool:
        request = Request(Command.PUMP_STOP)
        response = self.do_request(request)
        return response is not None

    def get_software_version(self) -> str | None:
        request = Request(Command.GET_SOFTWARE_VERSION)
        response = self.do_request(request)
        version = response.payload.decode('utf-8').strip('\x00') if response else None
        return version

    def get_serial_number(self) -> str | None:
        request = Request(Command.GET_SERIAL_NUMBER)
        response = self.do_request(request)
        serial = response.payload.decode('utf-8').strip('\x00') if response else None
        return serial

    def get_network_configuration(self) -> str | None:
        request = Request(Command.GET_NETWORK_CONFIGURATION)
        response = self.do_request(request)
        config = response.payload.decode('utf-8').strip('\x00') if response else None
        return config

    def set_setpoint(self, setpoint: int) -> bool:
        payload = struct.pack("<I", setpoint)
        request = Request(Command.SET_SETPOINT, payload=payload)
        response = self.do_request(request)
        return response is not None
