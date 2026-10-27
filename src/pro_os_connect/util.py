from crccheck.crc import Crc32Mpeg2


def calculate_crc(data: bytes) -> int:
    """Calculate CRC32/MPEG-2 for provided data bytes.

    The device computes the CRC over whole 32-bit words, so data whose length
    is not a multiple of 4 is zero-padded to the next word boundary first.

    Args:
        data: bytes to calculate CRC for

    Returns:
        int: computed CRC value
    """
    crc = Crc32Mpeg2()
    crc.process(data + b"\x00" * (-len(data) % 4))
    return crc.final()
