import logging
import select
import socket
import time
from datetime import datetime

from pro_os_connect.common import TelemetryFields

from .request import (
    EchoRequest,
    GetCpuSerialNumberRequest,
    GetNetworkConfigurationRequest,
    GetProtocolVersionRequest,
    GetSerialNumberRequest,
    GetSoftwareVersionRequest,
    GetTelemetryRequest,
    GetTimeRequest,
    PumpStartRequest,
    PumpStopRequest,
    RebootRequest,
    Request,
    SetSetpointRequest,
    SetTimeRequest,
)
from .response import (
    Response,
    ResponseError,
    ResponsePacket,
    ResponsePacketIncompleteError,
    StatusCode,
)

TCP_PORT = 2345


class ProOSConnectError(Exception):
    pass


class ProOSConnectRequestError(Exception):
    pass


class ProOSConnectConnectError(ProOSConnectRequestError):
    pass


class ProOSConnectTimeoutError(ProOSConnectRequestError):
    pass


class ProOSConnectResponseError(ProOSConnectError):
    pass


class ProOSConnect:
    def __init__(self, host: str, port: int = TCP_PORT, timeout: float = 2.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.socket = None
        self.logger = logging.getLogger(__name__)

    def connect(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.socket.connect((self.host, self.port))
            self.socket.setblocking(False)
        except (OSError, ConnectionRefusedError, socket.gaierror) as e:
            raise ProOSConnectConnectError(e)

    def disconnect(self):
        if self.socket:
            self.socket.close()
            self.socket = None

    def get_response_packet(self) -> ResponsePacket:
        readers = [self.socket]
        select_timeout = 0.1  # seconds
        resp_packet = ResponsePacket()
        start_time = time.time()
        while not resp_packet.is_valid() and (time.time() - start_time) < self.timeout:
            read_ready, _, _ = select.select(readers, [], [], select_timeout)
            if not read_ready:
                continue

            packet = self.socket.recv(4096)  # pyright: ignore[reportOptionalMemberAccess]
            self.logger.debug(f"Received packet: {packet.hex()}")
            if packet:
                resp_packet.append(packet)

        return resp_packet

    def send_request(self, request: Request) -> Response | None:
        if not self.socket:
            self.connect()

        request_packet = request.get_packet()
        self.logger.debug(f"Sending packet: {request_packet.hex()}")
        self.socket.sendall(request_packet)  # type: ignore

        self.logger.debug("Waiting for response...")
        response_packet = self.get_response_packet()

        try:
            response_packet.parse()
        except ResponsePacketIncompleteError:
            if not response_packet.has_data():
                raise ProOSConnectTimeoutError("Timeout waiting for response")
            else:
                raise ProOSConnectResponseError("Response packet incomplete")
        except ResponseError as e:
            raise ProOSConnectResponseError(e.description)

        if response_packet.status != StatusCode.OK.value:
            self.logger.debug(f"Response status: {response_packet.status}")
            raise ProOSConnectResponseError(
                f"Error in response: {StatusCode(response_packet.status).name}"
            )

        return response_packet.get_response()

    def get_protocol_version(self) -> int | None:
        response = self.send_request(GetProtocolVersionRequest())
        if not response or not isinstance(response.result, int):
            return None
        return response.result

    # FIXME: Doesn't work for some reason. No response received
    def echo(self, test_payload: bytes | None = None) -> bool:
        if test_payload is None:
            test_payload = b"Test Echo Payload"
        response = self.send_request(EchoRequest(test_payload))
        if response is None:
            return False
        return response.result == test_payload

    def reboot(self) -> bool:
        response = self.send_request(RebootRequest())
        return response is not None

    def get_telemetry(self) -> TelemetryFields | None:
        response = self.send_request(GetTelemetryRequest())
        return (
            response.result
            if response and isinstance(response.result, TelemetryFields)
            else None
        )

    def get_uptime(self) -> int | None:
        """Return device uptime in seconds."""
        fields = self.get_telemetry()
        return int(fields.timestamp / 100.0) if fields else None

    def is_running(self) -> bool | None:
        """Return True if the pump is running"""
        fields = self.get_telemetry()
        return bool(fields.is_running) if fields else None

    def get_time(self) -> datetime | None:
        """Return device time as datetime object"""
        response = self.send_request(GetTimeRequest())
        return (
            response.result
            if response and isinstance(response.result, datetime)
            else None
        )

    def set_time(self, date_obj: datetime) -> bool:
        response = self.send_request(SetTimeRequest(date_obj))
        return response is not None

    def pump_start(self) -> bool:
        response = self.send_request(PumpStartRequest())
        return response is not None

    def pump_stop(self) -> bool:
        response = self.send_request(PumpStopRequest())
        return response is not None

    # FIXME: Response payload CRC mismatch
    def get_software_version(self) -> str | None:
        response = self.send_request(GetSoftwareVersionRequest())
        return (
            response.result if response and isinstance(response.result, str) else None
        )

    # FIXME: Response payload CRC mismatch
    def get_serial_number(self) -> str | None:
        response = self.send_request(GetSerialNumberRequest())
        return (
            response.result if response and isinstance(response.result, str) else None
        )

    # FIXME: Response payload CRC mismatch
    def get_cpu_serial_number(self) -> str | None:
        response = self.send_request(GetCpuSerialNumberRequest())
        return response.result if response else None  # pyright: ignore[reportReturnType]

    def get_network_configuration(self) -> str | None:
        response = self.send_request(GetNetworkConfigurationRequest())
        return response.result if response else None  # pyright: ignore[reportReturnType]

    def set_setpoint(self, setpoint: int) -> bool:
        response = self.send_request(SetSetpointRequest(setpoint))
        return response is not None
