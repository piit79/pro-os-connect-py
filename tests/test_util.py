import pytest
from crccheck.crc import Crc32Mpeg2

from pro_os_connect.common import Command
from pro_os_connect.util import calculate_crc

from .helpers import CAPTURED, captured


def test_empty_data_returns_initial_value():
    assert calculate_crc(b"") == 0xFFFFFFFF


@pytest.mark.parametrize("data", [b"ABCD", b"12345678", bytes(range(256))])
def test_word_aligned_data_matches_plain_crc32_mpeg2(data):
    assert calculate_crc(data) == Crc32Mpeg2.calc(data)


@pytest.mark.parametrize("length", [1, 2, 3, 5, 6, 7, 9, 19, 23, 62])
def test_unaligned_data_is_zero_padded_to_word_boundary(length):
    data = bytes(range(1, length + 1))
    padded = data + b"\x00" * (-length % 4)
    assert len(padded) % 4 == 0
    assert calculate_crc(data) == Crc32Mpeg2.calc(padded)
    assert calculate_crc(data) != Crc32Mpeg2.calc(data)


def test_explicit_padding_gives_same_crc():
    assert calculate_crc(b"ABCDE") == calculate_crc(b"ABCDE\x00\x00\x00")


def test_returns_unsigned_32_bit_int():
    crc = calculate_crc(b"anything")
    assert isinstance(crc, int)
    assert 0 <= crc <= 0xFFFFFFFF


@pytest.mark.parametrize("command", list(CAPTURED), ids=lambda c: c.name)
def test_matches_crcs_from_real_device(command):
    packet = captured(command)
    payload_crc = int.from_bytes(packet[8:12], "little")
    header_crc = int.from_bytes(packet[12:16], "little")
    assert calculate_crc(packet[16:]) == payload_crc
    assert calculate_crc(packet[:12]) == header_crc


def test_captured_packets_cover_unaligned_lengths():
    """Guard that the fixtures still exercise the padding case"""
    assert any(len(captured(c)[16:]) % 4 for c in CAPTURED)
    assert Command.GET_SOFTWARE_VERSION in CAPTURED
