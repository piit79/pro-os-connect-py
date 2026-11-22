import json
import struct
from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import datetime
from typing import Generic, TypeVar

from .common import TelemetryFields

T = TypeVar("T")


def _check_type(data: object, expected: type | tuple[type, ...]) -> None:
    """Raise TypeError if data is not an instance of the expected type(s)"""
    if not isinstance(data, expected):
        types = expected if isinstance(expected, tuple) else (expected,)
        raise TypeError(f"Expected {' or '.join(t.__name__ for t in types)}, got {type(data).__name__}")


class Codec(ABC, Generic[T]):
    """Base class for the payload encoder/decoder classes"""

    @classmethod
    def empty(cls):
        return CodecEmpty()

    @classmethod
    def raw(cls):
        return CodecRaw()

    @classmethod
    def cstr(cls):
        return CodecCstr()

    @classmethod
    def struct(cls, format):
        return CodecStruct(format)

    @classmethod
    def telemetry_fields(cls):
        return CodecTelemetryFields()

    @classmethod
    def int(cls, format):
        return CodecInt(format)

    @classmethod
    def iso_datetime(cls):
        return CodecIsoDateTime()

    @classmethod
    def raw_hex(cls):
        return CodecRawHex()

    @classmethod
    def json_cstr(cls):
        return CodecCstrJson()

    @abstractmethod
    def pack(self, data: T) -> bytes:
        """Encode a value to bytes"""

    @abstractmethod
    def unpack(self, payload: bytes) -> T:
        """Decode bytes to a value"""


class CodecEmpty(Codec[None]):
    """Encoder/decoder class for no payload"""

    def pack(self, data: None) -> bytes:
        return b""

    def unpack(self, payload: bytes) -> None:
        return None


class CodecRaw(Codec[bytes]):
    """Encoder/decoder class for a raw unformatted payload"""

    def pack(self, data: bytes | bytearray) -> bytes:
        _check_type(data, (bytes, bytearray))
        return bytes(data)

    def unpack(self, payload: bytes) -> bytes:
        return payload


class CodecCstr(Codec[str]):
    """Encoder/decoder class for a null-terminated string payload"""

    def pack(self, data: str) -> bytes:
        _check_type(data, str)
        return data.encode("utf-8") + b"\x00"

    def unpack(self, payload: bytes) -> str:
        return payload.decode("utf-8").rstrip("\x00")


class CodecStruct(Codec[tuple]):
    """Encoder/decoder class for a struct payload"""

    def __init__(self, format: str) -> None:
        self.format = format

    def pack(self, data: tuple) -> bytes:
        return struct.pack(self.format, *data)

    def unpack(self, payload: bytes) -> tuple:
        return struct.unpack(self.format, payload)


class CodecTelemetryFields(Codec[TelemetryFields]):
    """Encoder/decoder class for a telemetry field struct payload"""

    def __init__(self) -> None:
        self._struct = CodecStruct("<IIIffffHBBBBBb")

    def pack(self, data: TelemetryFields) -> bytes:
        """Pack not implemented, this is only a decoder"""
        raise NotImplementedError()

    def unpack(self, payload: bytes) -> TelemetryFields:
        *values, is_running = self._struct.unpack(payload)
        return TelemetryFields.from_iterable((*values, bool(is_running)))


class CodecInt(Codec[int]):
    """Encoder/decoder class for a struct payload"""

    def __init__(self, format: str) -> None:
        self._struct = CodecStruct(format)

    def pack(self, data: int) -> bytes:
        if isinstance(data, bool):  # bool is an int subclass, but True/False is almost certainly a mistake here
            raise TypeError("Expected int, got bool")
        _check_type(data, int)
        try:
            return self._struct.pack((data,))
        except struct.error:
            raise ValueError(f"{data} is out of range for struct format {self._struct.format!r}") from None

    def unpack(self, payload: bytes) -> int:
        (val,) = self._struct.unpack(payload)
        return val


class CodecIsoDateTime(Codec[datetime]):
    """Encoder/decoder class for an ISO datetime payload in a null-terminated string"""

    def __init__(self, precision: int = 0) -> None:
        self.precision = precision
        self._cstr = CodecCstr()

    def pack(self, data: datetime) -> bytes:
        _check_type(data, datetime)
        length = 19 + (1 if self.precision > 0 else 0) + self.precision
        datetime_str = data.isoformat()[:length]
        return self._cstr.pack(datetime_str)

    def unpack(self, payload: bytes) -> datetime:
        datetime_str = self._cstr.unpack(payload)
        return datetime.fromisoformat(datetime_str)


class CodecRawHex(Codec[str]):
    """Encoder/decoder class for raw data represented as hex string"""

    def pack(self, data: str) -> bytes:
        return bytes.fromhex(data)

    def unpack(self, payload: bytes) -> str:
        return payload.hex()


class CodecCstrJson(Codec[Mapping]):
    """Encoder/decoder class for a JSON payload in a null-terminated string"""

    def __init__(self) -> None:
        self._cstr = CodecCstr()

    def pack(self, data: Mapping) -> bytes:
        json_str = json.dumps(data, indent=2)
        return self._cstr.pack(json_str)

    def unpack(self, payload: bytes) -> Mapping:
        data = json.loads(self._cstr.unpack(payload))
        if not isinstance(data, dict):
            raise ValueError(f"Expected a JSON object, got {type(data).__name__}")
        return data
