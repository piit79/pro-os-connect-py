import json
import struct
from abc import ABC
from datetime import datetime

from .common import TelemetryFields


class Codec(ABC):
    """Base class for the payload encoder/decoder classes"""

    @classmethod
    def null(cls):
        return CodecNull()

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

    def pack(self, data) -> bytes:
        raise NotImplementedError

    def unpack(self, payload: bytes):
        raise NotImplementedError


class CodecNull(Codec):
    """Encoder/decoder class for a raw unformatted payload"""

    def pack(self, data: bytes) -> bytes:
        return data

    def unpack(self, payload: bytes) -> bytes:
        return payload


class CodecCstr(Codec):
    """Encoder/decoder class for a null-terminated string payload"""

    def pack(self, data: str) -> bytes:
        return data.encode("utf-8") + b"\x00"

    def unpack(self, payload: bytes) -> str:
        return payload.decode("utf-8").rstrip("\x00")


class CodecStruct(Codec):
    """Encoder/decoder class for a struct payload"""

    def __init__(self, format) -> None:
        self.format = format

    def pack(self, data: tuple) -> bytes:
        return struct.pack(self.format, *data)

    def unpack(self, payload: bytes) -> tuple:
        return struct.unpack(self.format, payload)


class CodecTelemetryFields(CodecStruct):
    """Encoder/decoder class for a telemetry field struct payload"""

    def __init__(self) -> None:
        self.format = "<IIIffffHBBBBBb"

    def pack(self, data: tuple) -> bytes:
        """Pack not implemented, this is only a decoder"""
        raise NotImplementedError()

    def unpack(self, payload: bytes) -> tuple:
        return TelemetryFields(*super().unpack(payload))


class CodecInt(CodecStruct):
    """Encoder/decoder class for a struct payload"""

    def pack(self, data: int) -> bytes:  # pyright: ignore[reportIncompatibleMethodOverride]
        return super().pack((data,))

    def unpack(self, payload: bytes) -> int:  # pyright: ignore[reportIncompatibleMethodOverride]
        (val,) = super().unpack(payload)
        return val


class CodecIsoDateTime(CodecCstr):
    """Encoder/decoder class for an ISO datetime payload in a null-terminated string"""

    def pack(self, data: datetime) -> bytes:  # pyright: ignore[reportIncompatibleMethodOverride]
        datetime_str = data.isoformat()
        return super().pack(datetime_str)

    def unpack(self, payload: bytes) -> datetime:  # pyright: ignore[reportIncompatibleMethodOverride]
        datetime_str = super().unpack(payload)
        return datetime.fromisoformat(datetime_str)


class CodecRawHex(Codec):
    """Encoder/decoder class for raw data represented as hex string"""

    def pack(self, data: str) -> bytes:
        return bytes.fromhex(data)

    def unpack(self, payload: bytes) -> str:
        return payload.hex()


class CodecCstrJson(CodecCstr):
    """Encoder/decoder class for a JSON payload in a null-terminated string"""

    def pack(self, data: dict) -> bytes:  # pyright: ignore[reportIncompatibleMethodOverride]
        json_str = json.dumps(data, indent=2)
        return super().pack(json_str)

    def unpack(self, payload: bytes) -> dict:  # pyright: ignore[reportIncompatibleMethodOverride]
        json_str = super().unpack(payload)
        return json.loads(json_str)
