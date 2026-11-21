# pro-os-connect-py

Python library and command-line tool for monitoring and controlling
**Donaldson BOFA** fume extractors over the **Pro OS Connect** TCP protocol.

- Read telemetry: flow rate, filter pressures, blockage levels, alarms, pump state
- Start and stop the pump, change the setpoint
- Read and set the device clock
- Query device information: software version, serial numbers, network configuration
- No dependencies beyond [`crccheck`](https://pypi.org/project/crccheck/)

### Disclaimer

The initial working version of this library was written by a human. It was then refactored and improved with the help of Claude Code.

## Installation

```sh
pip install pro-os-connect-py
```

Requires Python 3.10 or newer. The device must be reachable on TCP port 2345.

## Command-line tool

```sh
pro-os-connect-cli --device bofa.local get_telemetry
```

```
timestamp=109951839
alarms=0
setpoint=370
flow_rate=9.2
combined_pressure=12.5
pre_pressure=11.1
inlet_pressure=15.4
pump_level=0
real_combined_blockage=0
real_pre_blockage=0
real_inlet_blockage=0
compensated_combined_blockage=0
compensated_pre_blockage=0
is_running=0
```

### Commands

| Command | Description |
|---|---|
| `get_telemetry` | All telemetry fields |
| `get_setpoint` | Current target flow rate |
| `set_setpoint VALUE` | Set the target flow rate (0–4294967295) and print the new value read back from the device |
| `is_running` | `1` if the pump is running, `0` otherwise |
| `pump_start` | Start the pump |
| `pump_stop` | Stop the pump |
| `get_uptime` | Device uptime in seconds |
| `get_time` | Device clock |
| `set_time` | Set the device clock to this computer's local time |
| `get_software_version` | Firmware version |
| `get_serial_number` | Device serial number |
| `get_cpu_serial_number` | CPU serial number (hex) |
| `get_network_configuration` | Network configuration |
| `get_protocol_version` | Pro OS Connect protocol version |
| `echo [--payload TEXT]` | Check connectivity and measure the round-trip time |
| `reboot` | Reboot the device |

### Options

| Option | Description |
|---|---|
| `--version` | Library version |
| `-d`, `--device HOST` | Hostname or IP address of the device (required) |
| `-j`, `--json` | Output JSON instead of text |
| `-p`, `--precision N` | Decimal places for telemetry values (default: 1) |
| `-l`, `--log-level LEVEL` | Log to stderr at `ERROR`, `WARNING`, `INFO` or `DEBUG` (`DEBUG` shows raw packets) |

Global options go before the command:

```sh
pro-os-connect-cli -d bofa.local --json get_time
```

```json
{
  "time": "2026-11-09T16:14:04"
}
```

In JSON mode, commands that return a single value use the command name without its
`get_`/`is_` prefix as the key (`{"time": ...}`, `{"running": false}`), and commands
that perform an action return `{"success": true}`. Errors are written as
`{"success": false, "error": "..."}`. In text mode, errors go to stderr.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 2 | Invalid command-line arguments |
| 5 | Request error: the connection failed or the device did not respond in time |
| 6 | Response error: the response was invalid, could not be decoded, or reported an error status |
| 7 | Internal error: the device responded with an unsupported command |
| 8 | Other client error |
| 9 | Command failed: invalid command, incorrect echo response |

## Library

```python
from pro_os_connect import ProOSConnect, ProOSConnectError

poc = ProOSConnect("bofa.local")
try:
    telemetry = poc.get_telemetry()
    print(f"Flow rate: {telemetry.flow_rate:.1f}, running: {bool(telemetry.is_running)}")

    setpoint = poc.set_setpoint(300)
    print(f"Setpoint set to {setpoint}")
    poc.pump_start()
except ProOSConnectError as e:
    print(f"Device error: {e}")
finally:
    poc.disconnect()
```

`ProOSConnect(host, port=2345, timeout=2.0, keep_connection=True)` connects on the first
request and keeps the connection open for later requests until `disconnect()` is called.
If a request fails, the connection is closed and the next request opens a new one.

The device accepts only one connection at a time. With `keep_connection=False`, the client
connects for each request and disconnects right after it, so other clients can connect
in between. To send several requests over one connection anyway, use `hold_connection()`:

```python
poc = ProOSConnect("bofa.local", keep_connection=False)
with poc.hold_connection():
    telemetry = poc.get_telemetry()
    clock = poc.get_time()
# Disconnected here
```

The connection is opened by the first request in the block and closed when the block
exits. Blocks can be nested. With `keep_connection=True`, `hold_connection()` has no effect.

### Methods

| Method | Returns |
|---|---|
| `get_telemetry()` | `TelemetryFields` |
| `get_uptime()` | `int`, seconds |
| `is_running()` | `bool` |
| `pump_start()`, `pump_stop()` | `None` |
| `get_setpoint()` | `int` |
| `set_setpoint(setpoint: int, read_back: bool = True)` | `int \| None`, the new setpoint as read from the device if `read_back=True`, or `None` |
| `get_time()` | `datetime` |
| `set_time(date_obj: datetime)` | `None`; sent without fractional seconds or time zone |
| `get_software_version()` | `str` |
| `get_serial_number()` | `str` |
| `get_cpu_serial_number()` | `str`, hex |
| `get_network_configuration()` | `dict` |
| `get_protocol_version()` | `int` |
| `echo(test_payload: bytes \| None = None)` | `bool`, whether the device sent the payload back unchanged |
| `reboot()` | `None` |
| `connect(force=False)`, `disconnect()` | Manage the connection explicitly |
| `hold_connection()` | Context manager: keep one connection open for the requests in the block |

Errors are raised as exceptions (see below), so methods that only perform an action return `None`.

`TelemetryFields` is a dataclass with these fields:

| Field | Type | Notes |
|---|---|---|
| `timestamp` | `int` | Device uptime in hundredths of a second |
| `alarms` | `int` | Alarm flags (bit map) |
| `setpoint` | `int` | Target flow rate [m³/h] |
| `flow_rate` | `float` | Actual flow rate [m³/h] |
| `combined_pressure`, `pre_pressure`, `inlet_pressure` | `float` | [Pa] |
| `pump_level` | `int` | Drive level (0 - 1000) |
| `real_combined_blockage`, `real_pre_blockage`, `real_inlet_blockage` | `int` | [%] |
| `compensated_combined_blockage`, `compensated_pre_blockage` | `int` | Blockage of the combined filter/pre-filter after accounting for the expected pressure drop across a new filter [%] |
| `is_running` | `int` | 1 while the pump is running, 0 when stopped |

### Errors

Errors communicating with the device derive from `ProOSConnectError`:

```
ProOSConnectError
├── ProOSConnectRequestError      sending the request failed
│   ├── ProOSConnectConnectError  connection refused, closed or broken
│   └── ProOSConnectTimeoutError  request not sent or no response within the timeout
├── ProOSConnectResponseError     invalid response, undecodable payload or error status
└── ProOSConnectInternalError     response command not supported by the library
```

Invalid arguments raise `TypeError` (wrong type, including `bool` where an `int` is expected)
or `ValueError` (out of range, e.g. a negative setpoint) before anything is sent, and leave
the connection untouched. They are bugs in the calling code, not device errors, so they
don't derive from `ProOSConnectError`.

### Thread safety

The `ProOSConnect` instance isn't thread-safe: two threads sharing one client could interleave
requests on the same socket

### Public API

The public API, covered by semantic versioning, is what `pro_os_connect` exports in `__all__`:
`ProOSConnect` with the methods and constructor arguments documented above, the exceptions,
`TelemetryFields`, `TCP_PORT` and `__version__`. Everything else, including `send_request()`,
the `request`, `response`, `codecs` and `common` modules, and attributes such as `socket`,
is internal and may change in any release.

## Protocol notes

The device's payload checksum is CRC-32/MPEG-2 computed over the data **zero-padded to
a multiple of 4 bytes**, as a 32-bit hardware CRC unit would compute it. A standard
CRC-32/MPEG-2 only matches when the length is already a multiple of 4. The device
silently ignores requests with any other checksum.

## Development

```sh
uv sync
uv run pytest
```

The tests do not need a device: they run against a simulated device on localhost.

## License

MIT
