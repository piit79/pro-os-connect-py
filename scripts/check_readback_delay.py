"""Find the delay needed before a newly set setpoint can be read back reliably.

Sets random setpoints on a real device, waits, reads the setpoint back and checks it matches. By default it starts
at a short delay and raises it by --step after every failure, until a whole run of --count cycles passes. That
delay is the shortest one that worked reliably. With --fixed, it tests each given --delay once instead.

By default every cycle opens a new connection, as each CLI invocation does, which is the slowest case.

The setpoint range is detected on startup by setting the lowest and highest possible values and reading back what
the device accepted. --min and --max narrow it down further. The original setpoint is restored at the end.

WARNING: this changes the setpoint of the device, including briefly to its minimum and maximum. Don't run it
against a device that is in use.

Examples:
    uv run scripts/check_readback_delay.py -d bofa.local
    uv run scripts/check_readback_delay.py -d bofa.local --min 150 --max 250 -n 200 --step 0.0005
    uv run scripts/check_readback_delay.py -d bofa.local --fixed --delay 0 0.005 0.05 --keep-connection
"""

import argparse
import random
import sys
import time
from dataclasses import dataclass

from pro_os_connect import SETPOINT_READBACK_DELAY, TCP_PORT, ProOSConnect, ProOSConnectError

SETPOINT_MAX = 2**32 - 1  # Largest value the protocol can carry
SETTLE_DELAY = 1.0  # Generous delay for setpoint changes that must be read back correctly (detection, restore)

DEFAULT_COUNT = 100
DEFAULT_FIXED_COUNT = 20
DEFAULT_START = 0.001
DEFAULT_STEP = 0.001
DEFAULT_MAX_DELAY = 1.0


@dataclass
class Result:
    delay: float
    cycles: int
    failures: int


def ms(seconds: float) -> str:
    return f"{seconds * 1000:g} ms"


def set_and_read(poc: ProOSConnect, value: int, delay: float, reconnect: bool) -> int:
    """Set the setpoint, wait and return the value read back"""
    if reconnect:
        poc.disconnect()  # The next request opens a new connection
    poc.set_setpoint(value, read_back=False)
    time.sleep(delay)
    return poc.get_setpoint()


def detect_range(poc: ProOSConnect) -> tuple[int, int]:
    """Return the lowest and highest setpoint the device accepts, by setting the extremes and reading them back"""
    low = set_and_read(poc, 0, SETTLE_DELAY, reconnect=False)
    high = set_and_read(poc, SETPOINT_MAX, SETTLE_DELAY, reconnect=False)
    return low, high


def random_setpoint(low: int, high: int, previous: int) -> int:
    """Return a random setpoint different from the previous one, so a stale read-back can't look correct"""
    while (value := random.randint(low, high)) == previous:
        pass
    return value


def check_delay(
    poc: ProOSConnect,
    delay: float,
    low: int,
    high: int,
    count: int,
    previous: int,
    verbose: bool,
    reconnect: bool,
    stop_on_failure: bool,
) -> tuple[Result, int]:
    """Run up to count cycles for one delay, starting from the previously set value.

    With stop_on_failure, stop at the first failed cycle. Return the result and the last value set.
    """
    failures = 0
    cycles = 0
    for cycles in range(1, count + 1):
        value = random_setpoint(low, high, previous)
        read = set_and_read(poc, value, delay, reconnect)
        if read == value:
            result = "ok"
        else:
            failures += 1
            result = "STALE (previous value)" if read == previous else "MISMATCH"
        if verbose or read != value:
            width = len(str(count))
            print(f"  [{cycles:>{width}}/{count}] set {value:>5}, read {read:>5}: {result}")
        previous = value
        if failures and stop_on_failure:
            break
    return Result(delay, cycles, failures), previous


def restore(poc: ProOSConnect, original: int, delay: float) -> None:
    """Set the original setpoint again and verify it after the given delay"""
    restored = set_and_read(poc, original, delay, reconnect=False)
    if restored == original:
        print(f"Setpoint restored to {original}")
    else:
        print(f"WARNING: setpoint reads {restored} after restoring {original}, check the device", file=sys.stderr)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--device", "-d", required=True, help="Device hostname or IP address")
    parser.add_argument("--port", type=int, default=TCP_PORT, help=f"Device TCP port (default: {TCP_PORT})")
    parser.add_argument("--min", type=int, dest="low", help="Lowest setpoint to use (default: detected minimum)")
    parser.add_argument("--max", type=int, dest="high", help="Highest setpoint to use (default: detected maximum)")
    parser.add_argument(
        "--count",
        "-n",
        type=int,
        help=f"Number of set/read cycles per delay (default: {DEFAULT_COUNT}, {DEFAULT_FIXED_COUNT} with --fixed)",
    )
    parser.add_argument(
        "--fixed",
        action="store_true",
        help="Test each --delay once instead of raising the delay until it passes",
    )
    parser.add_argument(
        "--delay",
        type=float,
        nargs="+",
        help=f"Delay in seconds to start at (default: {DEFAULT_START}); "
        f"with --fixed, the delay(s) to test (default: the library's {SETPOINT_READBACK_DELAY})",
    )
    parser.add_argument(
        "--step",
        type=float,
        default=DEFAULT_STEP,
        help=f"How much to raise the delay after a failure (default: {DEFAULT_STEP})",
    )
    parser.add_argument(
        "--max-delay",
        type=float,
        default=DEFAULT_MAX_DELAY,
        help=f"Give up when the delay would exceed this (default: {DEFAULT_MAX_DELAY})",
    )
    parser.add_argument(
        "--keep-connection",
        action="store_true",
        help="Use one connection for all cycles instead of a new one per cycle",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Print every cycle, not only failures")
    args = parser.parse_args()

    if args.low is not None and args.high is not None and args.low >= args.high:
        parser.error("--min must be lower than --max")
    if args.count is None:
        args.count = DEFAULT_FIXED_COUNT if args.fixed else DEFAULT_COUNT
    if args.count < 1:
        parser.error("--count must be at least 1")
    if args.delay is None:
        args.delay = [SETPOINT_READBACK_DELAY] if args.fixed else [DEFAULT_START]
    if any(d < 0 for d in args.delay):
        parser.error("--delay must not be negative")
    if not args.fixed:
        if len(args.delay) != 1:
            parser.error("a single starting --delay is expected (use --fixed to test several delays)")
        if args.step <= 0:
            parser.error("--step must be positive")
        if args.max_delay < args.delay[0]:
            parser.error("--max-delay must not be lower than the starting --delay")
    return args


def main() -> int:
    args = parse_args()
    reconnect = not args.keep_connection
    mode = "new connection per cycle" if reconnect else "one connection"

    poc = ProOSConnect(args.device, args.port)
    original = None
    results: list[Result] = []
    try:
        original = previous = poc.get_setpoint()
        print(f"Original setpoint: {original}")

        print("Detecting setpoint range...")
        detected_low, detected_high = detect_range(poc)
        previous = detected_high
        print(f"Device accepts setpoints {detected_low}-{detected_high}")
        low = detected_low if args.low is None else min(max(args.low, detected_low), detected_high)
        high = detected_high if args.high is None else min(max(args.high, detected_low), detected_high)
        if (args.low is not None and args.low != low) or (args.high is not None and args.high != high):
            print(f"Requested range clamped to {low}-{high}")
        if low >= high:
            print(f"Error: setpoint range {low}-{high} needs at least two values", file=sys.stderr)
            return 2

        def run(delay: float, previous: int) -> tuple[Result, int]:
            print(f"Delay {ms(delay)}, up to {args.count} cycles, setpoints {low}-{high}, {mode}:")
            return check_delay(
                poc, delay, low, high, args.count, previous, args.verbose, reconnect, stop_on_failure=not args.fixed
            )

        if args.fixed:
            for delay in args.delay:
                result, previous = run(delay, previous)
                results.append(result)
        else:
            start, step = args.delay[0], args.step
            attempt = 0
            # Compute each delay from the start instead of adding up, so float errors don't accumulate
            while (delay := round(start + attempt * step, 9)) <= args.max_delay:
                result, previous = run(delay, previous)
                results.append(result)
                if not result.failures:
                    break
                attempt += 1
    except ProOSConnectError as e:
        print(f"Device error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130
    finally:
        if original is not None:
            try:
                restore(poc, original, max([SETTLE_DELAY, *(r.delay for r in results)]))
            except ProOSConnectError as e:
                print(f"WARNING: failed to restore setpoint {original}: {e}", file=sys.stderr)
        poc.disconnect()

    print("\nSummary:")
    delay_width = max((len(ms(r.delay)) for r in results), default=0)
    count_width = len(str(args.count))
    for r in results:
        status = "OK" if r.failures == 0 else "FAIL"
        passed = f"{r.cycles - r.failures:>{count_width}}/{r.cycles:>{count_width}}"
        print(f"  {ms(r.delay):>{delay_width}}: {passed} correct  {status}")

    if args.fixed:
        return 0 if all(r.failures == 0 for r in results) else 1
    if results and results[-1].failures == 0:
        print(f"\nShortest reliable delay: {ms(results[-1].delay)} ({args.count} cycles without a failure)")
        return 0
    print(f"\nNo reliable delay found up to {ms(args.max_delay)}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
