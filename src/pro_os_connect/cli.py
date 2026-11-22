import argparse
import json
import logging
import sys
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from time import perf_counter
from typing import Any

from . import (
    ProOSConnect,
    ProOSConnectError,
    ProOSConnectInternalError,
    ProOSConnectRequestError,
    ProOSConnectResponseError,
    __version__,
)

UNSIGNED_LONG_MAX = 2**32 - 1
LOG_HANDLER_NAME = "pro_os_connect.cli"


class CliError(Exception):
    """Exception raised from the ProOSConnectCli class"""


def unsigned_long(value: str) -> int:
    """Custom argparse type for non-negative integers."""
    try:
        ivalue = int(value)
        if ivalue < 0:
            raise ValueError
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value} is not a valid non-negative integer") from None
    if ivalue > UNSIGNED_LONG_MAX:
        raise argparse.ArgumentTypeError(f"{value} is greater than the maximum allowed value of {UNSIGNED_LONG_MAX}")
    return ivalue


@dataclass(frozen=True)
class CliCommand:
    command: str
    help: str = ""
    args: Iterable[tuple[tuple, Mapping[str, Any]]] = ()


COMMANDS = [
    CliCommand("get_protocol_version", help="Get Pro OS Connect protocol version"),
    CliCommand(
        "echo",
        help="Send an echo request",
        args=((("--payload",), {"type": str, "help": "Test payload for echo command"}),),
    ),
    CliCommand("reboot", help="Reboot the device"),
    CliCommand("get_telemetry", help="Get device telemetry"),
    CliCommand("get_setpoint", help="Get current target flow rate"),
    CliCommand("get_uptime", help="Get device uptime in seconds"),
    CliCommand("is_running", help="Check whether the pump is running (1/0)"),
    CliCommand("get_time", help="Get the device time"),
    CliCommand("set_time", help="Set the device time to the current time"),
    CliCommand("pump_start", help="Start the pump"),
    CliCommand("pump_stop", help="Stop the pump"),
    CliCommand("get_software_version", help="Get the device software version"),
    CliCommand("get_serial_number", help="Get the device serial number"),
    CliCommand("get_cpu_serial_number", help="Get the main CPU serial number"),
    CliCommand("get_network_configuration", help="Get the device network configuration"),
    CliCommand(
        "set_setpoint",
        help="Set the target flow rate",
        args=((("setpoint",), {"type": unsigned_long, "help": f"Flow rate to set (0-{UNSIGNED_LONG_MAX})"}),),
    ),
]


def output_text(data: str | bool | Mapping, error=False):
    """Output data as formatted text."""
    stream = sys.stderr if error else sys.stdout
    if isinstance(data, Mapping):
        for key, value in data.items():
            if isinstance(value, bool):  # 1/0, like a bool result below
                value = int(value)
            print(f"{key}={value}", file=stream)
    elif isinstance(data, bool):
        print(1 if data else 0, file=stream)
    else:
        print(data, file=stream)


def output_json(data: Mapping):
    """Output data as formatted JSON."""
    print(json.dumps(data, indent=2))


def json_result_key(command: str) -> str:
    for prefix in ("get_", "is_"):
        if command.startswith(prefix):
            return command.removeprefix(prefix)
    return "success"


class ProOSConnectCli:
    DONE = object()

    def __init__(self):
        self.args = self.get_args()
        self.command: str = self.args.command
        self.poc = ProOSConnect(self.args.device)
        self.setup_logging()

    def get_args(self):
        parser = argparse.ArgumentParser(description="Pro-OS Connect CLI")
        parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
        parser.add_argument(
            "--device",
            "-d",
            type=str,
            help="Hostname or IP address of the Pro OS Connect device",
            required=True,
        )
        parser.add_argument("--json", "-j", action="store_true", help="Output in JSON format")
        parser.add_argument(
            "--precision",
            "-p",
            type=int,
            default=1,
            help="Display precision in decimal digits",
        )
        parser.add_argument(
            "--log-level",
            "-l",
            choices=["ERROR", "WARNING", "INFO", "DEBUG"],
            help="Logging level",
        )
        subparsers = parser.add_subparsers(required=True, help="Command to execute", dest="command")

        for cmd in COMMANDS:
            cmd_parser = subparsers.add_parser(cmd.command, help=cmd.help)
            for args, kwargs in cmd.args:
                cmd_parser.add_argument(*args, **kwargs)

        return parser.parse_args()

    def setup_logging(self):
        """Log to stderr through a single handler on the library logger.

        The logger is shared by the whole process, so a handler added by an earlier ProOSConnectCli is replaced
        instead of adding another one, which would print every message once per instance.
        """
        logger = self.poc.logger
        for old in [h for h in logger.handlers if h.get_name() == LOG_HANDLER_NAME]:
            logger.removeHandler(old)
        handler = logging.StreamHandler(stream=sys.stderr)  # The current stderr, not an earlier instance's
        handler.set_name(LOG_HANDLER_NAME)
        handler.setFormatter(logging.Formatter(fmt="{levelname}: {message}", style="{"))
        logger.addHandler(handler)
        if self.args.log_level:
            self.poc.logger.setLevel(self.args.log_level)

    def output(self, data, json_data: Mapping | None = None, error=False):
        if self.args.json:
            if json_data is None:
                if isinstance(data, Mapping):
                    json_data = data
                else:
                    if isinstance(data, datetime):
                        data = data.isoformat()
                    json_data = {json_result_key(self.command): data}
            output_json(json_data)
        else:
            output_text(data, error=error)

    def error(self, error_msg):
        self.output(error_msg, {"success": False, "error": error_msg}, error=True)

    def echo(self):
        test_payload = self.args.payload.encode("utf-8") if self.args.payload else None
        start_time = perf_counter()
        res = self.poc.echo(test_payload)
        if res:
            rtt = round((perf_counter() - start_time) * 1000, 2)  # Round trip time in milliseconds
            self.output(f"Echo response received in {rtt} ms", {"success": True, "time": rtt})
        else:
            raise CliError("Echo response incorrect")
        return self.DONE

    def get_telemetry(self) -> dict[str, Any]:
        fields = self.poc.get_telemetry()
        res = asdict(fields)
        for f, v in res.items():
            if isinstance(v, float):
                res[f] = round(v, self.args.precision)
        return res

    def set_time(self) -> None:
        """Set time to current time"""
        return self.poc.set_time(datetime.now())

    def set_setpoint(self):
        setpoint = self.poc.set_setpoint(self.args.setpoint)
        self.output(
            f"Setpoint set to {setpoint}",
            {"success": True, "setpoint": setpoint},
        )
        return self.DONE

    def pro_os_command(self):
        try:
            method = getattr(self.poc, self.command)
        except AttributeError:
            raise CliError(f"Invalid command: {self.command}") from None

        return method()

    def run(self) -> int:
        method = getattr(self, self.command, self.pro_os_command)
        exit_code = 0

        try:
            result = method()
        except ProOSConnectRequestError as e:
            self.error(f"Error sending request: {e}")
            exit_code = 5
        except ProOSConnectResponseError as e:
            self.error(f"Response error: {e}")
            exit_code = 6
        except ProOSConnectInternalError as e:
            self.error(f"Internal error: {e}")
            exit_code = 7
        except ProOSConnectError as e:
            # Catch all for the future, just in case
            self.error(f"Client error: {e}")
            exit_code = 8
        except CliError as e:
            self.error(str(e))
            exit_code = 9
        else:
            if result is None:  # Action methods return None on success
                result = True
            if result is not self.DONE:
                self.output(result)
        finally:
            self.poc.disconnect()

        return exit_code


def main():
    cli = ProOSConnectCli()
    exit_code = cli.run()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
