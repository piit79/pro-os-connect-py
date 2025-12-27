import argparse
import json
from typing import Mapping

from . import ProOSConnect


COMMANDS = {
    "get_protocol_version": [],
    "echo": [
        (("--payload", "-p"), {"type": str, "help": "Test payload for echo command"})
    ],
    "reboot": [],
    "get_telemetry": [],
    "is_running": [],
    "get_time": [],
    "pump_start": [],
    "pump_stop": [],
    "get_software_version": [],
    "get_serial_number": [],
    "get_network_configuration": [],
    "get_setpoint": [],
    "set_setpoint": [
        (("setpoint",), {"type": int, "help": "Setpoint value to set"})
    ],
}


def output_text(data: Mapping):
    """Output data as formatted text."""
    if isinstance(data, Mapping):
        for key, value in data.items():
            print(f"{key}={value}")
    else:
        print(data)


def output_json(data: Mapping | None):
    """Output data as formatted JSON."""
    print(json.dumps(data, indent=2))


class ProOSConnectCli:
    def __init__(self):
        self.args = self.get_args()
        self.command: str = self.args.command
        self.poc = ProOSConnect(self.args.device)
        self.result = None

    def get_args(self):
        parser = argparse.ArgumentParser(description="Pro-OS Connect CLI")
        parser.add_argument("--device", "-d", type=str, help="Hostname or IP address of the Pro OS Connect device", required=True)
        parser.add_argument("--json", "-j", action="store_true", help="Output JSON format")
        subparsers = parser.add_subparsers(required=True, help="Command to execute", dest="command")

        for cmd_name, cmd_args in COMMANDS.items():
            cmd_parser = subparsers.add_parser(cmd_name, help=f'{cmd_name} help')
            for args, kwargs in cmd_args:
                cmd_parser.add_argument(*args, **kwargs)

        return parser.parse_args()

    def output(self, text_data, json_data: Mapping | None = None):
        if json_data is None:
            json_data = text_data
        if self.args.json:
            output_json(json_data)
        else:
            output_text(text_data)

    def error(self, error_msg):
        self.output(error_msg, {"result": False, "error": error_msg})

    def echo(self):
        test_payload = self.args.payload.encode('utf-8') if self.args.payload else None
        res = self.poc.echo(test_payload)
        print(f"Echo result: {res}")

    def get_telemetry(self):
        fields = self.poc.get_telemetry()
        if fields:
            self.output(fields._asdict())

    def is_running_result(self):
        self.output(1 if self.result else 0, {"running": self.result})

    def get_setpoint(self):
        fields = self.poc.get_telemetry()
        if fields:
            self.output(fields.setpoint, {"setpoint": fields.setpoint})

    def set_setpoint(self):
        res = self.poc.set_setpoint(self.args.setpoint)
        text_res = f"Setpoint set to {self.args.setpoint}" if res else "Error setting setpoint"
        self.output(text_res, {"result": res, "setpoint": self.args.setpoint})

    def run(self):
        method = getattr(self, self.command, None) or getattr(self.poc, self.command, None)
        if not method:
            self.error(f"Unknown command: {self.command}")
            return

        self.result = method()
        if result_method := getattr(self, f"{self.command}_result", None):
            result_method()
        else:
            if self.result is None:
                self.error("Command failed, no result received")
            else:
                self.output(self.result, {"result": self.result})


def main():
    cli = ProOSConnectCli()
    cli.run()


if __name__ == "__main__":
    main()
