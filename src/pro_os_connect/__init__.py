import logging
import socket
import time
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version

from .common import StatusCode, TelemetryFields
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
    ResponseCommandNotImplementedError,
    ResponseDecodeError,
    ResponseError,
    ResponsePacket,
    ResponsePacketIncompleteError,
)

# The public API. Everything else, including the request, response, codecs and common modules, is internal and may
# change in any release.
__all__ = [
    "TCP_PORT",
    "ProOSConnect",
    "ProOSConnectConnectError",
    "ProOSConnectError",
    "ProOSConnectInternalError",
    "ProOSConnectRequestError",
    "ProOSConnectResponseError",
    "ProOSConnectStatusError",
    "ProOSConnectTimeoutError",
    "StatusCode",
    "TelemetryFields",
    "__version__",
]

try:
    __version__ = version("pro-os-connect-py")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0+unknown"

TCP_PORT = 2345
SETPOINT_READBACK_DELAY = 0.010  # Wait 10 ms before reading back new setpoint


class ProOSConnectError(Exception):
    """Base class for all Pro OS Connect errors."""


class ProOSConnectRequestError(ProOSConnectError):
    """Error raised while sending or handling a request."""


class ProOSConnectConnectError(ProOSConnectRequestError):
    """Error raised while connecting to the device."""


class ProOSConnectTimeoutError(ProOSConnectRequestError):
    """Error raised when a timeout occurs while waiting for a response."""


class ProOSConnectResponseError(ProOSConnectError):
    """Error raised when a response is received but indicates an error."""


class ProOSConnectStatusError(ProOSConnectResponseError):
    """Error raised when the device responds with an error status code.

    :ivar status: The status code from the response, e.g. StatusCode.WRITE_PROTECTED
    """

    def __init__(self, status: StatusCode):
        super().__init__(f"Error in response: {status.name}")
        self.status = status

    def __reduce__(self):  # Exceptions pickle their args, which here is the message, not the status
        return type(self), (self.status,)


class ProOSConnectInternalError(ProOSConnectError):
    """Error raised when an internal error occurs."""


class ProOSConnect:
    """Main class for connecting to a Pro OS device and sending requests."""

    def __init__(self, host: str, port: int = TCP_PORT, timeout: float = 2.0, keep_connection: bool = True):
        """
        :param keep_connection: Keep the connection open between requests until disconnect() is called. If False,
            connect for each request and disconnect after it, so other clients can connect in between (the device
            accepts only one connection at a time).
        """
        self.host = host
        self.port = port
        self.timeout = timeout
        self.keep_connection = keep_connection
        self.socket: socket.socket | None = None
        self._hold_depth = 0  # Number of open hold_connection() blocks
        self.logger = logging.getLogger(__name__)

    def connect(self, force: bool = False):
        if self.socket:
            if not force:
                self.logger.debug("Already connected to %s:%d", self.host, self.port)
                return
            else:
                self.logger.debug("Force reconnecting to %s:%d", self.host, self.port)
                self.disconnect()

        try:
            # Keep the socket in timeout mode: sendall() then waits for buffer space instead of failing with
            # BlockingIOError on a non-blocking socket. _get_response_packet() narrows it to an overall deadline.
            self.socket = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as e:
            raise ProOSConnectConnectError(e) from e

    def disconnect(self):
        if self.socket:
            self.socket.close()
            self.socket = None

    @contextmanager
    def hold_connection(self) -> Generator["ProOSConnect", None, None]:
        """Send all requests in the block over one connection, even with keep_connection=False.

        The connection is opened by the first request in the block. If a request fails, the next one reconnects.
        With keep_connection=False, the connection is closed when the outermost block exits. Blocks can be nested.
        """
        self._hold_depth += 1
        try:
            yield self
        finally:
            self._hold_depth -= 1
            if self._hold_depth == 0 and not self.keep_connection:
                self.disconnect()

    def send_request(self, request: Request) -> Response:
        self.connect()

        err = True
        try:
            response = self._send_request(request)
        except TimeoutError as e:  # socket.timeout; must come before OSError, its base class
            raise ProOSConnectTimeoutError(f"Timeout sending request: {e}") from e
        except OSError as e:
            raise ProOSConnectConnectError(e) from e
        except ResponseCommandNotImplementedError as e:
            raise ProOSConnectInternalError(e) from e
        except ResponseDecodeError as e:
            raise ProOSConnectResponseError(e) from e
        else:
            err = False
            self.logger.debug("Request sent and response received successfully")
        finally:
            if err or not (self.keep_connection or self._hold_depth):
                self.disconnect()

        return response

    def _send_request(self, request: Request) -> Response:
        assert self.socket is not None  # to prevent type checker errors in sendall() below
        request_packet = request.get_packet()
        self.logger.debug("Sending packet: %s", request_packet.hex())
        self.socket.sendall(request_packet)

        self.logger.debug("Waiting for response...")
        response_packet = self._get_response_packet()

        try:
            response_packet.parse()
        except ResponsePacketIncompleteError:
            if not response_packet.has_data():
                raise ProOSConnectTimeoutError("Timeout waiting for response") from None
            else:
                raise ProOSConnectResponseError("Response packet incomplete") from None
        except ResponseError as e:
            raise ProOSConnectResponseError(f"{e.description}: {e}") from e

        if response_packet.status != StatusCode.OK:
            self.logger.debug("Response status: %s", response_packet.status.name)
            raise ProOSConnectStatusError(response_packet.status)

        response = response_packet.get_response()
        if response.COMMAND != request.COMMAND:
            raise ProOSConnectResponseError(
                f"Response command {response.COMMAND.name} does not match request {request.COMMAND.name}"
            )
        return response

    def _get_response_packet(self) -> ResponsePacket:
        """Receive until the response packet is complete or the timeout runs out.

        The timeout applies to the whole response, not to each recv(): every recv() only gets the time left until
        the deadline, so a device sending the response slowly in pieces can't extend the wait.
        Return the packet received so far; the caller tells a timeout (no data) from an incomplete packet.
        """
        assert self.socket is not None  # to prevent type checker errors below
        resp_packet = ResponsePacket()
        # Monotonic clock: a wall-clock step (e.g. an NTP correction) must not shorten or extend the timeout
        deadline = time.monotonic() + self.timeout
        try:
            while not resp_packet.is_valid():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.socket.settimeout(remaining)
                try:
                    data = self.socket.recv(4096)
                except TimeoutError:  # Caught here, so send_request() doesn't report it as a send timeout
                    break
                if not data:
                    raise ProOSConnectConnectError("Connection closed by device")
                self.logger.debug("Received packet: %s", data.hex())
                resp_packet.append(data)
        finally:
            self.socket.settimeout(self.timeout)  # Restore the full timeout for the next sendall()

        return resp_packet

    def get_protocol_version(self) -> int:
        response = self.send_request(GetProtocolVersionRequest())
        return response.result

    def echo(self, test_payload: bytes | None = None) -> bool:
        if test_payload is None:
            test_payload = b"Test Echo Payload"
        response = self.send_request(EchoRequest(test_payload))
        return response.result == test_payload

    def reboot(self) -> None:
        self.send_request(RebootRequest())

    def get_telemetry(self) -> TelemetryFields:
        response = self.send_request(GetTelemetryRequest())
        return response.result

    def get_uptime(self) -> int:
        """Return device uptime in seconds."""
        fields = self.get_telemetry()
        return fields.timestamp // 100

    def is_running(self) -> bool:
        """Return True if the pump is running"""
        fields = self.get_telemetry()
        return fields.is_running

    def get_time(self) -> datetime:
        """Return device time as datetime object"""
        response = self.send_request(GetTimeRequest())
        return response.result

    def set_time(self, date_obj: datetime) -> None:
        self.send_request(SetTimeRequest(date_obj))

    def pump_start(self) -> None:
        self.send_request(PumpStartRequest())

    def pump_stop(self) -> None:
        self.send_request(PumpStopRequest())

    def get_software_version(self) -> str:
        response = self.send_request(GetSoftwareVersionRequest())
        return response.result

    def get_serial_number(self) -> str:
        response = self.send_request(GetSerialNumberRequest())
        return response.result

    def get_cpu_serial_number(self) -> str:
        response = self.send_request(GetCpuSerialNumberRequest())
        return response.result

    def get_network_configuration(self) -> dict:
        response = self.send_request(GetNetworkConfigurationRequest())
        return response.result

    def get_setpoint(self) -> int:
        fields = self.get_telemetry()
        return fields.setpoint

    def set_setpoint(self, setpoint: int, read_back: bool = True) -> int | None:
        """Set setpoint and return setpoint read from device, or None if not read_back"""
        with self.hold_connection():
            self.send_request(SetSetpointRequest(setpoint))
            if read_back:
                time.sleep(SETPOINT_READBACK_DELAY)
                return self.get_setpoint()
        return None
