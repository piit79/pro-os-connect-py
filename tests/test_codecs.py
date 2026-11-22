import json
import struct
from datetime import datetime

import pytest

from pro_os_connect.codecs import (
    Codec,
    CodecCstr,
    CodecCstrJson,
    CodecEmpty,
    CodecInt,
    CodecIsoDateTime,
    CodecRaw,
    CodecRawHex,
    CodecStruct,
    CodecTelemetryFields,
)
from pro_os_connect.common import TelemetryFields

CODEC_FACTORIES = [
    (Codec.empty, (), CodecEmpty),
    (Codec.raw, (), CodecRaw),
    (Codec.cstr, (), CodecCstr),
    (Codec.struct, ("<I",), CodecStruct),
    (Codec.telemetry_fields, (), CodecTelemetryFields),
    (Codec.int, ("<I",), CodecInt),
    (Codec.iso_datetime, (), CodecIsoDateTime),
    (Codec.raw_hex, (), CodecRawHex),
    (Codec.json_cstr, (), CodecCstrJson),
]


def test_all_codecs_are_tested():
    assert {codec_cls for _, _, codec_cls in CODEC_FACTORIES} == set(Codec.__subclasses__())


@pytest.mark.parametrize(("factory", "args", "expected"), CODEC_FACTORIES)
def test_factories_return_matching_codec(factory, args, expected):
    assert type(factory(*args)) is expected


def test_struct_factory_keeps_format():
    assert Codec.struct("<HB").format == "<HB"
    assert Codec.int(">H")._struct.format == ">H"


def test_base_codec_is_abstract():
    with pytest.raises(TypeError, match="Can't instantiate abstract class"):
        Codec()  # pyright: ignore[reportAbstractUsage] - intentional for the test


class TestCodecRaw:
    @pytest.mark.parametrize("data", [b"", b"\x00", b"Test Echo Payload", bytes(range(256))])
    def test_passes_bytes_through(self, data):
        codec = CodecRaw()
        assert codec.pack(data) == data
        assert codec.unpack(data) == data

    def test_pack_accepts_bytearray(self):
        packed = CodecRaw().pack(bytearray(b"abc"))
        assert packed == b"abc"
        assert type(packed) is bytes

    @pytest.mark.parametrize("data", ["text", None, 1, [1, 2]])
    def test_pack_wrong_type_raises(self, data):
        with pytest.raises(TypeError, match=f"Expected bytes or bytearray, got {type(data).__name__}"):
            CodecRaw().pack(data)


class TestCodecCstr:
    def test_pack_appends_null_terminator(self):
        assert CodecCstr().pack("v2.0.0") == b"v2.0.0\x00"

    def test_pack_empty_string(self):
        assert CodecCstr().pack("") == b"\x00"

    def test_pack_encodes_utf8(self):
        assert CodecCstr().pack("Žluťoučký") == "Žluťoučký".encode() + b"\x00"

    @pytest.mark.parametrize("data", [b"v2.0.0", None, 1])
    def test_pack_wrong_type_raises(self, data):
        with pytest.raises(TypeError, match=f"Expected str, got {type(data).__name__}"):
            CodecCstr().pack(data)

    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            (b"v2.0.0\x00", "v2.0.0"),
            (b"v2.0.0", "v2.0.0"),
            (b"v2.0.0\x00\x00\x00", "v2.0.0"),
            (b"\x00", ""),
            (b"", ""),
        ],
    )
    def test_unpack_strips_trailing_nulls(self, payload, expected):
        assert CodecCstr().unpack(payload) == expected

    def test_roundtrip(self):
        codec = CodecCstr()
        assert codec.unpack(codec.pack("2511000618")) == "2511000618"

    def test_unpack_invalid_utf8_raises(self):
        with pytest.raises(UnicodeDecodeError):
            CodecCstr().unpack(b"\xff\xfe\x00")


class TestCodecStruct:
    def test_roundtrip(self):
        codec = CodecStruct("<IhB")
        packed = codec.pack((1, -2, 3))
        assert packed == struct.pack("<IhB", 1, -2, 3)
        assert codec.unpack(packed) == (1, -2, 3)

    def test_unpack_wrong_size_raises(self):
        with pytest.raises(struct.error):
            CodecStruct("<I").unpack(b"\x00\x00")

    def test_pack_wrong_arg_count_raises(self):
        with pytest.raises(struct.error):
            CodecStruct("<II").pack((1,))


class TestCodecInt:
    @pytest.mark.parametrize("value", [0, 1, 150, 0xFFFFFFFF])
    def test_roundtrip(self, value):
        codec = CodecInt("<I")
        assert codec.pack(value) == struct.pack("<I", value)
        assert codec.unpack(codec.pack(value)) == value

    def test_little_endian(self):
        assert CodecInt("<I").pack(1) == b"\x01\x00\x00\x00"

    @pytest.mark.parametrize(("fmt", "value"), [("<I", -1), ("<I", 2**32), ("<B", 256), ("<b", -129)])
    def test_out_of_range_raises_value_error(self, fmt, value):
        with pytest.raises(ValueError, match=f"{value} is out of range for struct format '{fmt}'"):
            CodecInt(fmt).pack(value)

    @pytest.mark.parametrize("value", [True, False, 1.0, "1", None, b"\x01"])
    def test_wrong_type_raises_type_error(self, value):
        with pytest.raises(TypeError, match=f"Expected int, got {type(value).__name__}"):
            CodecInt("<I").pack(value)


class TestCodecTelemetryFields:
    FIELDS = (10370300, 1, 150, 5.25, 13.5, 15.75, 13.0, 512, 1, 2, 3, 4, 5, -1)

    def test_unpack_to_dataclass(self):
        payload = struct.pack("<IIIffffHBBBBBb", *self.FIELDS)
        assert len(payload) == 36
        result = CodecTelemetryFields().unpack(payload)
        assert isinstance(result, TelemetryFields)
        assert result == TelemetryFields(*self.FIELDS[:-1], True)
        assert result.timestamp == 10370300
        assert result.alarms == 1
        assert result.setpoint == 150
        assert result.flow_rate == 5.25
        assert result.pump_level == 512
        assert result.compensated_pre_blockage == 5
        assert result.is_running is True  # Any non-zero value means running

    @pytest.mark.parametrize(("raw", "expected"), [(0, False), (1, True)])
    def test_is_running_is_bool(self, raw, expected):
        payload = struct.pack("<IIIffffHBBBBBb", *self.FIELDS[:-1], raw)
        assert CodecTelemetryFields().unpack(payload).is_running is expected

    def test_pack_not_supported(self):
        with pytest.raises(NotImplementedError):
            CodecTelemetryFields().pack(TelemetryFields.from_iterable(self.FIELDS))

    def test_unpack_short_payload_raises(self):
        with pytest.raises(struct.error):
            CodecTelemetryFields().unpack(b"\x00" * 35)


class TestCodecIsoDateTime:
    DT = datetime(2026, 10, 27, 19, 27, 46, 123456)

    def test_pack_default_precision_drops_fraction(self):
        assert CodecIsoDateTime().pack(self.DT) == b"2026-10-27T19:27:46\x00"

    @pytest.mark.parametrize(
        ("precision", "expected"),
        [
            (1, b"2026-10-27T19:27:46.1\x00"),
            (3, b"2026-10-27T19:27:46.123\x00"),
            (6, b"2026-10-27T19:27:46.123456\x00"),
        ],
    )
    def test_pack_with_precision(self, precision, expected):
        assert CodecIsoDateTime(precision).pack(self.DT) == expected

    def test_pack_drops_timezone(self):
        from datetime import timezone

        dt = self.DT.replace(microsecond=0, tzinfo=timezone.utc)
        assert CodecIsoDateTime().pack(dt) == b"2026-10-27T19:27:46\x00"

    def test_unpack(self):
        result = CodecIsoDateTime().unpack(b"2026-10-27T19:27:46\x00")
        assert result == datetime(2026, 10, 27, 19, 27, 46)

    def test_roundtrip(self):
        codec = CodecIsoDateTime()
        assert codec.unpack(codec.pack(self.DT)) == self.DT.replace(microsecond=0)

    def test_unpack_invalid_raises(self):
        with pytest.raises(ValueError):
            CodecIsoDateTime().unpack(b"not a date\x00")

    @pytest.mark.parametrize("data", ["2026-10-27T19:27:46", None, 1761593266])
    def test_pack_wrong_type_raises(self, data):
        with pytest.raises(TypeError, match=f"Expected datetime, got {type(data).__name__}"):
            CodecIsoDateTime().pack(data)


class TestCodecRawHex:
    def test_unpack(self):
        assert CodecRawHex().unpack(b"\x57\x67\x07\x2d") == "5767072d"

    def test_pack(self):
        assert CodecRawHex().pack("5767072d") == b"\x57\x67\x07\x2d"

    def test_empty(self):
        assert CodecRawHex().unpack(b"") == ""
        assert CodecRawHex().pack("") == b""

    def test_pack_invalid_hex_raises(self):
        with pytest.raises(ValueError):
            CodecRawHex().pack("xyz")


class TestCodecCstrJson:
    def test_unpack_device_payload(self):
        payload = b'{\n    "manual": {},\n    "write_enabled": true\n  }\x00'
        assert CodecCstrJson().unpack(payload) == {"manual": {}, "write_enabled": True}

    def test_pack(self):
        data = {"a": 1, "b": [True, None]}
        packed = CodecCstrJson().pack(data)
        assert packed.endswith(b"\x00")
        assert packed[:-1] == json.dumps(data, indent=2).encode()

    def test_roundtrip(self):
        codec = CodecCstrJson()
        data = {"dhcp": True, "ip": "10.0.0.2", "nested": {"x": [1, 2]}}
        assert codec.unpack(codec.pack(data)) == data

    def test_unpack_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            CodecCstrJson().unpack(b"{oops\x00")

    @pytest.mark.parametrize("data", [(b"null\x00"), (b"42\x00"), (b'"foo"\x00'), (b"[1,2]\x00")])
    def test_unpack_non_object_raises(self, data):
        with pytest.raises(ValueError):
            CodecCstrJson().unpack(data)
