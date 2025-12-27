from crccheck.crc import Crc32Mpeg2


def calculate_crc(data: bytes) -> int:
    """Calculate CRC32/MPEG-2 for provided data bytes.

    Args:
        data: bytes to calculate CRC for

    Returns:
        int: computed CRC value
    """
    crc = Crc32Mpeg2()
    crc.process(data)
    return crc.final()
