from enum import Enum


FRAMING_PHRASE = 0xB0FAB0FA


class Node(Enum):
    PRO_OS = 0x0001
    NETWORK_CARD = 0x0002
    CLIENT = 0x0003


class Command(Enum):
    GET_PROTOCOL_VERSION = 0x00010001
    ECHO = 0x00010002
    REBOOT = 0x00010003
    GET_TELEMETRY = 0x00010004
    GET_TIME = 0x00010006
    SET_TIME = 0x00010007
    SOFTWARE_UPDATE_START = 0x00010008
    SOFTWARE_UPDATE_FLASH_PAGE = 0x00010009
    SOFTWARE_UPDATE_FINISH = 0x0001000A
    FILE_GET_START = 0x0001000C
    FILE_GET_PAGE = 0x0001000D
    FILE_GET_FINISH = 0x0001000E
    PUMP_START = 0x0001000F
    PUMP_STOP = 0x00010010
    GET_SOFTWARE_VERSION = 0x00010011
    GET_SERIAL_NUMBER = 0x00010012
    GET_CPU_SERIAL_NUMBER = 0x00010013
    GET_NETWORK_CONFIGURATION = 0x00010014
    SET_DISPLAYED_IP_ADDRESS = 0x00010015  # Not intended for use by end client device
    SET_CLIENT_TIMEOUT = 0x00010016
    SET_SETPOINT = 0x00010017


class StatusCode(Enum):
    OK = 0x00000000  # Success status, no errors, packet processed successfully
    NOT_PROCESSED = 0x0FFFFFFF  # The initial packet request status
    INTERNAL_ERROR = 0xB0000000  # General error code, usually if internal process has failed in some way
    INTERNAL_NULL_PTR = 0xB0000001  # If an internal process errored out due to null pointer
    INTERNAL_BAD_ARG = 0xB0000002  # If an internal process errored out due to bad argument
    INTERNAL_OVERFLOW = 0xB0000003  # If an internal process produced a potential buffer overflow
    INTERNAL_TIMEOUT = 0xB0000004  # If an internal process timed-out
    INVALID_ADDRESS = 0xC0000000  # Command Header’s To-From address was incorrectly set
    INVALID_STATUS = 0xC0000001  # Command Header’s Status code was incorrectly set
    INVALID_COMMAND = 0xC0000002  # Command was not found or not supported
    WRITE_PROTECTED = 0xC0000003  # If Command was rejected because the iQ2 is configured in a read only state
    COMMAND_BAD_ARG = 0xE0000000  # Requested command has invalid argument
    COMMAND_NOT_IN_PROGRESS = 0xE0000001  # Requested command has been called out of order (PROTOCOL_COMMAND_FILE_GET ***)
    COMMAND_WRONG_SEQUENCE = 0xE0000002  # Command sequence is invalid
    COMMAND_CRC_INVALID = 0xE0000003  # Command CRC mismatch (PROTOCOL_COMMAND_SW_FINISH)
    COMMAND_FILE_EMPTY = 0xE0000004  # Requested File does not exist or is empty
