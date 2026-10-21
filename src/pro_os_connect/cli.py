import argparse
import json
import logging
import sys
from collections.abc import Mapping
from datetime import datetime
from time import sleep, time

from . import ProOSConnect, ProOSConnectRequestError, ProOSConnectResponseError
from .common import Node

COMMANDS = {
    "get_protocol_version": [],
    "echo": [
        (("--payload", "-p"), {"type": str, "help": "Test payload for echo command"})
    ],
    "reboot": [],
    "get_telemetry": [],
    "get_setpoint": [],
    "get_uptime": [],
    "is_running": [],
    "get_time": [],
    "set_time": [(("time",), {"type": str, "help": "Time to set"})],
    "pump_start": [],
    "pump_stop": [],
    "get_software_version": [],
    "get_serial_number": [],
    "get_cpu_serial_number": [],
    "get_network_configuration": [],
    "set_setpoint": [(("setpoint",), {"type": int, "help": "Setpoint value to set"})],
}


def output_text(data: str | bool | Mapping, error=False):
    """Output data as formatted text."""
    stream = sys.stderr if error else sys.stdout
    if isinstance(data, Mapping):
        for key, value in data.items():
            print(f"{key}={value}", file=stream)
    elif isinstance(data, bool):
        print(1 if data else 0, file=stream)
    else:
        print(data, file=stream)


def output_json(data: Mapping):
    """Output data as formatted JSON."""
    print(json.dumps(data, indent=2))


class ProOSConnectCli:
    DONE = object()

    def __init__(self):
        self.args = self.get_args()
        self.command: str = self.args.command
        self.poc = ProOSConnect(self.args.device)
        self.setup_logging()
        self.json_result_key: str | None = None

    def get_args(self):
        parser = argparse.ArgumentParser(description="Pro-OS Connect CLI")
        parser.add_argument(
            "--device",
            "-d",
            type=str,
            help="Hostname or IP address of the Pro OS Connect device",
            required=True,
        )
        parser.add_argument(
            "--json", "-j", action="store_true", help="Output in JSON format"
        )
        parser.add_argument(
            "--node",
            "-n",
            choices=[Node.PRO_OS.name, Node.NETWORK_CARD.name],
            help="Destination node",
        )
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
        subparsers = parser.add_subparsers(
            required=True, help="Command to execute", dest="command"
        )

        for cmd_name, cmd_args in COMMANDS.items():
            cmd_parser = subparsers.add_parser(cmd_name, help=f"{cmd_name} help")
            for args, kwargs in cmd_args:
                cmd_parser.add_argument(*args, **kwargs)

        return parser.parse_args()

    def setup_logging(self):
        handler = logging.StreamHandler(stream=sys.stderr)
        handler.setFormatter(logging.Formatter(fmt="{levelname}: {message}", style="{"))
        self.poc.logger.addHandler(handler)
        if self.args.log_level:
            self.poc.logger.setLevel(self.args.log_level)

    def ensure_json_result_key(self):
        if self.json_result_key is None:
            if self.command.startswith("get_"):
                self.json_result_key = self.command[4:]
            elif self.command.startswith("is_"):
                self.json_result_key = self.command[3:]
            else:
                self.json_result_key = "result"

    def output(self, data, json_data: Mapping | None = None, error=False):
        if self.args.json:
            if json_data is None:
                if isinstance(data, Mapping):
                    json_data = data
                else:
                    if isinstance(data, datetime):
                        data = data.isoformat()
                    json_data = {self.json_result_key: data}
            output_json(json_data)
        else:
            output_text(data, error=error)

    def error(self, error_msg):
        self.output(error_msg, {"success": False, "error": error_msg}, error=True)

    def echo(self):
        test_payload = self.args.payload.encode("utf-8") if self.args.payload else None
        start_time = time()
        res = self.poc.echo(test_payload)
        if res:
            rtt = round(
                (time() - start_time) * 100, 2
            )  # Round trim time in milliseconds
            self.output(
                f"Echo response received in {rtt} ms", {"result": True, "time": rtt}
            )

    def get_telemetry(self):
        fields = self.poc.get_telemetry()
        res = fields
        if fields:
            res = fields._asdict()
            for f, v in res.items():
                if isinstance(v, float):
                    res[f] = round(v, self.args.precision)
        return res

    def set_time(self):
        """Set time to current time"""
        self.poc.set_time(datetime.now())

    def get_network_configuration(self):
        return self.poc.get_network_configuration()

    def get_setpoint(self) -> int | None:
        fields = self.poc.get_telemetry()
        return fields.setpoint if fields else None

    def set_setpoint(self):
        res = self.poc.set_setpoint(self.args.setpoint)
        if not res:
            self.error("Error setting setpoint")
            sys.exit(1)
        sleep(0.01)  # Wait a bit otherwise the old setpoint will be returned
        setpoint = self.get_setpoint()
        if not setpoint:
            self.error("Error getting new setpoint")
            sys.exit(1)
        self.output(
            f"Setpoint set to {setpoint}",
            {"success": res, "setpoint": setpoint},
        )
        return self.DONE

    def pro_os_command(self):
        method = getattr(self.poc, self.command)
        if method is None:
            self.error(f"Invalid command: {self.command}")
            return

        return method()

    def run(self):
        method = getattr(self, self.command, self.pro_os_command)

        try:
            result = method()
        except ProOSConnectRequestError as e:
            self.error(f"Error sending request: {e}")
            sys.exit(5)
        except ProOSConnectResponseError as e:
            self.error(f"Response error: {e}")
            sys.exit(6)

        match result:
            case self.DONE:
                # Output was handled by the method
                return
            case None:
                self.error("Command failed, no result received")
                sys.exit(7)

        self.ensure_json_result_key()
        self.output(result)


def main():
    cli = ProOSConnectCli()
    cli.run()


if __name__ == "__main__":
    main()
