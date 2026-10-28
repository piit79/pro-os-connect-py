import functools
import json
import logging
from datetime import datetime

import pytest

from pro_os_connect import ProOSConnect, cli
from pro_os_connect.cli import COMMANDS, ProOSConnectCli, main, output_json, output_text
from pro_os_connect.common import Command, StatusCode

from .helpers import build_packet


@pytest.fixture(autouse=True)
def restore_logger():
    """The CLI adds a handler and sets a level on the library logger, undo it"""
    logger = logging.getLogger("pro_os_connect")
    handlers, level = logger.handlers[:], logger.level
    yield
    logger.handlers[:] = handlers
    logger.setLevel(level)


@pytest.fixture
def clients(device, monkeypatch):
    """Point the CLI at the fake device and collect the clients it creates"""
    created: list[ProOSConnect] = []

    def factory(host, port=device.port):
        poc = ProOSConnect(host, port, timeout=0.3)
        created.append(poc)
        return poc

    monkeypatch.setattr(cli, "ProOSConnect", factory)
    yield created
    # The CLI relies on process exit to close the connection
    for poc in created:
        poc.disconnect()


@pytest.fixture
def run(clients, monkeypatch, capsys):
    """Run the CLI against the fake device and return (exit code, stdout, stderr)"""

    def runner(*args: str) -> tuple[str | int | None, str, str]:
        monkeypatch.setattr("sys.argv", ["pro-os-connect-cli", "-d", "127.0.0.1", *args])
        try:
            main()
            code = 0
        except SystemExit as e:
            code = e.code
        finally:
            for poc in clients:
                poc.disconnect()
        out, err = capsys.readouterr()
        return code, out, err

    return runner


class TestOutputHelpers:
    def test_output_text_string(self, capsys):
        output_text("hello")
        assert capsys.readouterr() == ("hello\n", "")

    @pytest.mark.parametrize(("value", "expected"), [(True, "1\n"), (False, "0\n")])
    def test_output_text_bool(self, capsys, value, expected):
        output_text(value)
        assert capsys.readouterr().out == expected

    def test_output_text_mapping(self, capsys):
        output_text({"a": 1, "b": "x"})
        assert capsys.readouterr().out == "a=1\nb=x\n"

    def test_output_text_error_goes_to_stderr(self, capsys):
        output_text("oops", error=True)
        assert capsys.readouterr() == ("", "oops\n")

    def test_output_json(self, capsys):
        output_json({"a": [1, True, None]})
        assert json.loads(capsys.readouterr().out) == {"a": [1, True, None]}


class TestArguments:
    def test_device_is_required(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["pro-os-connect-cli", "get_time"])
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2
        assert "--device" in capsys.readouterr().err

    def test_command_is_required(self, run):
        code, _, err = run()
        assert code == 2
        assert "command" in err

    def test_unknown_command(self, run):
        code, _, err = run("self_destruct")
        assert code == 2
        assert "invalid choice" in err

    def test_set_setpoint_requires_int(self, run):
        code, _, err = run("set_setpoint", "high")
        assert code == 2
        assert "invalid int value" in err

    def test_invalid_log_level(self, run):
        code, _, _ = run("-l", "TRACE", "get_time")
        assert code == 2

    @pytest.mark.parametrize("command", list(COMMANDS))
    def test_every_command_exists(self, command):
        assert hasattr(ProOSConnectCli, command) or hasattr(ProOSConnect, command)

    def test_defaults(self, run, simulated, monkeypatch):
        seen = {}
        original_run = ProOSConnectCli.run

        def spy(self):
            seen.update(vars(self.args))
            original_run(self)

        monkeypatch.setattr(ProOSConnectCli, "run", spy)
        run("get_time")
        assert seen == {
            "device": "127.0.0.1",
            "json": False,
            "node": None,
            "precision": 1,
            "log_level": None,
            "command": "get_time",
        }

    def test_log_level(self, run, simulated):
        run("--log-level", "DEBUG", "get_time")
        assert logging.getLogger("pro_os_connect").level == logging.DEBUG

    def test_log_output_goes_to_stderr(self, run, simulated):
        code, out, err = run("-l", "DEBUG", "get_software_version")
        assert code == 0
        assert out == "v2.0.0\n"
        assert "DEBUG: Sending packet:" in err


class TestTextOutput:
    @pytest.mark.parametrize(
        ("args", "expected"),
        [
            (["get_protocol_version"], "1"),
            (["get_software_version"], "v2.0.0"),
            (["get_serial_number"], "2511000618"),
            (["get_cpu_serial_number"], "5767072d39314637424e79137d2e5454"),
            (["get_time"], "2026-10-27 19:27:46"),
            (["get_setpoint"], "150"),
            (["get_uptime"], "103627"),
            (["is_running"], "0"),
            (["pump_start"], "1"),
            (["pump_stop"], "1"),
            (["set_time"], "1"),
            (["reboot"], "1"),
        ],
    )
    def test_simple_commands(self, run, simulated, device, args, expected):
        if args == ["reboot"]:
            device.respond(build_packet(Command.REBOOT))
        code, out, err = run(*args)
        assert (code, out, err) == (0, expected + "\n", "")

    def test_get_network_configuration(self, run, simulated):
        code, out, _ = run("get_network_configuration")
        assert code == 0
        assert out == "manual={}\nwrite_enabled=True\n"

    def test_get_telemetry(self, run, simulated):
        code, out, _ = run("get_telemetry")
        assert code == 0
        lines = out.splitlines()
        assert lines[0] == "timestamp=10362745"
        assert "setpoint=150" in lines
        assert "flow_rate=5.0" in lines
        assert "pre_pressure=17.5" in lines
        assert lines[-1] == "is_running=0"
        assert len(lines) == 14

    @pytest.mark.parametrize(
        ("precision", "expected"), [(0, "flow_rate=5.0"), (2, "flow_rate=4.96"), (4, "flow_rate=4.9571")]
    )
    def test_get_telemetry_precision(self, run, simulated, precision, expected):
        _, out, _ = run("-p", str(precision), "get_telemetry")
        assert expected in out.splitlines()

    def test_set_time_sends_current_time(self, run, simulated):
        before = datetime.now().replace(microsecond=0)
        run("set_time")
        after = datetime.now()
        assert before <= datetime.fromisoformat(simulated.time) <= after

    def test_pump_start_changes_state(self, run, simulated):
        run("pump_start")
        assert simulated.running
        _, out, _ = run("is_running")
        assert out == "1\n"

    def test_set_setpoint(self, run, simulated, monkeypatch):
        monkeypatch.setattr(cli, "sleep", lambda seconds: None)
        code, out, _ = run("set_setpoint", "200")
        assert (code, out) == (0, "Setpoint set to 200\n")
        assert simulated.setpoint == 200

    def test_echo(self, run, simulated, device):
        code, out, _ = run("echo")
        assert code == 0
        assert out.startswith("Echo response received in ")
        assert out.endswith(" ms\n")

    def test_echo_custom_payload(self, run, simulated, device):
        from .helpers import parse_request

        run("echo", "--payload", "Žluťoučký kůň")
        assert parse_request(device.requests[-1])[3] == "Žluťoučký kůň".encode()

    def test_echo_incorrect(self, run, device):
        device.respond(build_packet(Command.ECHO, b"wrong"))
        code, out, err = run("echo")
        assert (code, out, err) == (0, "", "Echo response incorrect\n")


class TestJsonOutput:
    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            ("get_protocol_version", {"protocol_version": 1}),
            ("get_software_version", {"software_version": "v2.0.0"}),
            ("get_serial_number", {"serial_number": "2511000618"}),
            ("get_cpu_serial_number", {"cpu_serial_number": "5767072d39314637424e79137d2e5454"}),
            ("get_time", {"time": "2026-10-27T19:27:46"}),
            ("get_setpoint", {"setpoint": 150}),
            ("get_uptime", {"uptime": 103627}),
            ("is_running", {"running": False}),
            ("pump_start", {"success": True}),
            ("pump_stop", {"success": True}),
            ("set_time", {"success": True}),
            ("get_network_configuration", {"manual": {}, "write_enabled": True}),
        ],
    )
    def test_commands(self, run, simulated, command, expected):
        code, out, err = run("--json", command)
        assert (code, err) == (0, "")
        assert json.loads(out) == expected

    def test_get_telemetry(self, run, simulated):
        _, out, _ = run("-j", "-p", "2", "get_telemetry")
        data = json.loads(out)
        assert data["setpoint"] == 150
        assert data["flow_rate"] == 4.96
        assert data["combined_pressure"] == 19.69
        assert data["is_running"] == 0
        assert len(data) == 14

    def test_set_setpoint(self, run, simulated, monkeypatch):
        monkeypatch.setattr(cli, "sleep", lambda seconds: None)
        _, out, _ = run("-j", "set_setpoint", "120")
        assert json.loads(out) == {"success": True, "setpoint": 120}

    def test_echo(self, run, simulated, monkeypatch):
        _, out, _ = run("-j", "echo")
        data = json.loads(out)
        assert data["success"] is True
        assert isinstance(data["time"], float)

    @pytest.mark.xfail(reason="Echo round trip time is multiplied by 100 instead of 1000 to get ms")
    def test_echo_round_trip_time_in_ms(self, run, simulated, monkeypatch):
        rtt_ms = 542.0
        clock_start = 10.000
        clock = iter([clock_start, clock_start + rtt_ms / 1000])
        monkeypatch.setattr(cli, "time", lambda: next(clock))
        _, out, _ = run("-j", "echo")
        assert json.loads(out)["time"] == rtt_ms

    def test_echo_incorrect(self, run, device):
        device.respond(build_packet(Command.ECHO, b"wrong"))
        code, out, _ = run("-j", "echo")
        assert code == 0
        assert json.loads(out) == {"success": False, "error": "Echo response incorrect"}


class TestErrors:
    def test_timeout_exits_5(self, run, device):
        device.respond()
        code, out, err = run("get_time")
        assert code == 5
        assert out == ""
        assert err == "Error sending request: Timeout waiting for response\n"

    def test_connection_refused_exits_5(self, run, monkeypatch):
        monkeypatch.setattr(cli, "ProOSConnect", functools.partial(cli.ProOSConnect, port=1))
        code, _, err = run("get_time")
        assert code == 5
        assert err.startswith("Error sending request: ")

    def test_error_status_exits_6(self, run, simulated):
        simulated.status = StatusCode.WRITE_PROTECTED
        code, _, err = run("pump_start")
        assert code == 6
        assert err == "Response error: Error in response: WRITE_PROTECTED\n"

    def test_invalid_response_exits_6(self, run, device):
        device.respond(build_packet(Command.GET_TIME, b"abcd", payload_crc=0))
        code, _, err = run("get_time")
        assert code == 6
        assert "Response error: Response payload CRC error" in err

    def test_no_result_exits_7(self, run, device):
        # A response to a different command leaves the getter without a result
        device.respond(build_packet(Command.GET_SERIAL_NUMBER, b"123\x00"))
        code, _, err = run("get_telemetry")
        assert code == 7
        assert err == "Command failed, no result received\n"

    def test_errors_as_json(self, run, simulated):
        simulated.status = StatusCode.INVALID_COMMAND
        code, out, err = run("--json", "get_time")
        assert code == 6
        assert err == ""
        assert json.loads(out) == {
            "success": False,
            "error": "Response error: Error in response: INVALID_COMMAND",
        }

    def test_set_setpoint_rejected(self, run, simulated):
        simulated.status = StatusCode.WRITE_PROTECTED
        code, _, err = run("set_setpoint", "100")
        assert code == 6
        assert "WRITE_PROTECTED" in err

    def test_set_setpoint_reports_failure(self, run, simulated, monkeypatch):
        monkeypatch.setattr(ProOSConnect, "set_setpoint", lambda self, setpoint: False)
        code, _, err = run("set_setpoint", "100")
        assert code == 1
        assert err == "Error setting setpoint\n"

    def test_set_setpoint_cannot_read_back(self, run, simulated, monkeypatch):
        monkeypatch.setattr(cli, "sleep", lambda seconds: None)
        monkeypatch.setattr(ProOSConnect, "get_telemetry", lambda self: None)
        code, _, err = run("set_setpoint", "100")
        assert code == 1
        assert err == "Error getting new setpoint\n"

    def test_echo_without_response_exits_7(self, run, simulated, monkeypatch):
        monkeypatch.setattr(ProOSConnect, "echo", lambda self, payload: None)
        code, _, err = run("echo")
        assert code == 7
        assert err == "Command failed, no result received\n"

class TestInternalError:
    def test_unexpected_command_in_response(self, run, device):
        device.respond(build_packet(Command.SOFTWARE_UPDATE_START))
        code, out, err = run("get_time")
        assert code not in (0, None)
        assert out == ""
        assert "not implemented" in err
