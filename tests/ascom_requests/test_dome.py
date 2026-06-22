"""
Dome Hardware Test Suite
========================
Tests core dome functionality against the ASCOM Alpaca server.
Designed to be run at the observatory with eyes on the hardware.

Usage:
    python test_dome.py                       # Run all tests in order
    python test_dome.py --simulate            # Run against simulated server (no stop on failure)
    python test_dome.py --list                # List available tests
    python test_dome.py --test connect        # Run a single test
    python test_dome.py --test connect read_status slew_azimuth

In hardware mode (default):
  - Read-only tests continue on failure (safe — nothing moves)
  - Movement tests stop on first failure (hardware safety)

In simulate mode (--simulate):
  - All tests run independently regardless of failure
"""

import argparse
from pathlib import Path
import sys
import time

# Ensure the project root is on sys.path so `ascom_requests` is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ascom_requests import dome


# ── Helpers ──────────────────────────────────────────────────────────

AZ_TOLERANCE = 12.0       # degrees — dome encoder has ~10° error per testing notes
POLL_INTERVAL = 2         # seconds between slewing polls
SLEW_TIMEOUT = 120        # seconds max wait for a dome slew
HOME_TIMEOUT = 180        # seconds max wait for find-home (may need full revolution)


class TestFailure(Exception):
    pass


def header(msg):
    width = max(len(msg) + 4, 60)
    print()
    print("=" * width)
    print(f"  {msg}")
    print("=" * width)


def step(msg):
    print(f"\n  >> {msg}")


def info(msg):
    print(f"     {msg}")


def observe(msg):
    """Print a message telling the operator what to visually confirm."""
    print(f"\n  ** OBSERVE: {msg}")


def passed(msg):
    print(f"  [PASS] {msg}")


def check(condition, pass_msg, fail_msg):
    if condition:
        passed(pass_msg)
    else:
        raise TestFailure(fail_msg)


def ensure_connected():
    """Connect to the dome if not already connected."""
    if not dome.get_connected():
        step("Dome not connected — connecting first...")
        dome.set_connected(True)
        check(dome.get_connected() is True, "Connected successfully", "Failed to connect")


def wait_until_stopped(timeout=SLEW_TIMEOUT):
    """Poll slewing status until the dome stops or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        if not dome.get_slewing():
            return True
        elapsed = int(time.time() - start)
        az = dome.get_azimuth()
        info(f"  dome moving... ({elapsed}s)  Az={az:.1f}°")
        time.sleep(POLL_INTERVAL)
    raise TestFailure(f"Dome still slewing after {timeout}s — possible hang.")


# ── Tests ────────────────────────────────────────────────────────────

def test_connect():
    """Connect to the dome and verify the connection."""
    header("TEST: Connect to Dome")

    step("Connecting...")
    dome.set_connected(True)

    connected = dome.get_connected()
    check(connected is True, "Connected successfully", "set_connected(True) did not connect")

    name = dome.get_name()
    info(f"Dome name: {name}")
    check(name is not None and len(str(name)) > 0, f"Got dome name: {name}", "Dome name is empty")


def test_read_status():
    """Read all basic status properties — nothing moves, read-only."""
    header("TEST: Read Basic Status (read-only, nothing moves)")
    ensure_connected()

    props = {
        "Name":             dome.get_name,
        "Description":      dome.get_description,
        "Driver Info":      dome.get_driverinfo,
        "Driver Version":   dome.get_driverversion,
        "Interface Ver":    dome.get_interfaceversion,
        "Azimuth":          dome.get_azimuth,
        "At Home":          dome.get_athome,
        "At Park":          dome.get_atpark,
        "Slewing":          dome.get_slewing,
        "Slaved":           dome.get_slaved,
        "Shutter Status":   dome.get_shutterstatus,
    }

    for label, fn in props.items():
        val = fn()
        info(f"{label:20s} = {val}")
        check(val is not None, f"{label} returned a value", f"{label} returned None")

    az = dome.get_azimuth()
    check(0 <= az < 360, f"Azimuth in valid range [0,360): {az:.1f}°", f"Azimuth out of range: {az}")

    observe(f"The dome reports azimuth = {az:.1f}°. Look at the physical slit direction and compare.")
    observe("Dome encoder accuracy is ~10° — some offset is normal.")


def test_capabilities():
    """Read all capability flags and verify they return booleans."""
    header("TEST: Capability Flags")
    ensure_connected()

    caps = {
        "CanFindHome":      dome.get_canfindhome,
        "CanPark":          dome.get_canpark,
        "CanSetAltitude":   dome.get_cansetaltitude,
        "CanSetAzimuth":    dome.get_cansetazimuth,
        "CanSetPark":       dome.get_cansetpark,
        "CanSetShutter":    dome.get_cansetshutter,
        "CanSlave":         dome.get_canslave,
        "CanSyncAzimuth":   dome.get_cansyncazimuth,
    }

    for label, fn in caps.items():
        val = fn()
        info(f"{label:20s} = {val}")
        check(isinstance(val, bool), f"{label} returned bool", f"{label} returned non-bool: {type(val)}")


def test_find_home():
    """Run the home-finding sequence (searches for reed switch index)."""
    header("TEST: Find Home")
    ensure_connected()

    observe("The dome is about to search for its home (index) position.")
    observe("It may rotate up to a full revolution. Stand clear of the dome edge.")

    step("Sending findhome command...")
    dome.findhome()

    step("Waiting for home search to complete (this can take a while)...")
    start = time.time()
    while time.time() - start < HOME_TIMEOUT:
        slewing = dome.get_slewing()
        at_home = dome.get_athome()
        az = dome.get_azimuth()
        elapsed = int(time.time() - start)

        if at_home:
            info(f"  Home found at Az={az:.1f}° after {elapsed}s")
            break

        if not slewing and not at_home:
            # Dome stopped but didn't find home — might need a moment
            info(f"  dome stopped, waiting... ({elapsed}s)  Az={az:.1f}°")
        else:
            info(f"  searching... ({elapsed}s)  Az={az:.1f}°")

        time.sleep(POLL_INTERVAL)
    else:
        raise TestFailure(f"Dome did not find home within {HOME_TIMEOUT}s")

    check(dome.get_athome() is True, "Dome reports at home position", "athome is not True after findhome")

    observe("The dome should have stopped at its home/index position. Confirm visually.")


def test_slew_azimuth():
    """Slew the dome to a specific azimuth and verify arrival."""
    header("TEST: Slew to Azimuth")
    ensure_connected()

    cur_az = dome.get_azimuth()
    info(f"Current azimuth: {cur_az:.1f}°")

    # Pick a target 90 degrees away (enough to see obvious motion)
    target_az = (cur_az + 90.0) % 360.0
    info(f"Target azimuth:  {target_az:.1f}°")

    observe(f"The dome is about to rotate to {target_az:.1f}°. Watch it start moving.")
    observe("Stand clear of the dome edge!")

    step("Sending slew command...")
    dome.slew_to_azimuth(target_az)

    # Give it a moment to start
    time.sleep(3)

    step("Waiting for dome to reach target...")
    wait_until_stopped()

    final_az = dome.get_azimuth()
    az_err = abs(final_az - target_az)
    if az_err > 180:
        az_err = 360 - az_err
    info(f"Final azimuth:   {final_az:.1f}°")
    info(f"Target was:      {target_az:.1f}°")
    info(f"Error:           {az_err:.1f}° (tolerance: {AZ_TOLERANCE}°)")

    check(az_err < AZ_TOLERANCE, f"Azimuth within tolerance ({az_err:.1f}° < {AZ_TOLERANCE}°)", f"Azimuth error too large: {az_err:.1f}°")

    observe(f"The dome should now be pointing at ~{target_az:.0f}°. Confirm the slit direction matches.")


def test_slew_second_target():
    """Slew to a second azimuth to verify repeated slews work."""
    header("TEST: Slew to Second Target")
    ensure_connected()

    cur_az = dome.get_azimuth()
    # Go to 180° (South) — a recognizable direction
    target_az = 180.0
    if abs(cur_az - target_az) < 20:
        target_az = 0.0  # go to North instead if already near South
    info(f"Current azimuth: {cur_az:.1f}°")
    info(f"Target azimuth:  {target_az:.1f}° ({'South' if target_az == 180 else 'North'})")

    observe(f"The dome is about to rotate to {target_az:.0f}° ({'South' if target_az == 180 else 'North'}). Watch it move.")

    step("Sending slew command...")
    dome.slew_to_azimuth(target_az)
    time.sleep(3)

    step("Waiting for dome to reach target...")
    wait_until_stopped()

    final_az = dome.get_azimuth()
    az_err = abs(final_az - target_az)
    if az_err > 180:
        az_err = 360 - az_err
    info(f"Final azimuth:   {final_az:.1f}°")
    info(f"Error:           {az_err:.1f}°")

    check(az_err < AZ_TOLERANCE, f"Azimuth within tolerance ({az_err:.1f}° < {AZ_TOLERANCE}°)", f"Azimuth error too large: {az_err:.1f}°")

    observe("Confirm the dome slit is now facing approximately "
            + ("South." if target_az == 180 else "North."))


def test_abort_slew():
    """Start a slew and abort it. Verify the dome stops."""
    header("TEST: Abort Slew")
    ensure_connected()

    cur_az = dome.get_azimuth()
    # Pick a target far enough that we have time to abort
    target_az = (cur_az + 180.0) % 360.0
    info(f"Slewing to distant target {target_az:.1f}° (opposite side)...")

    observe("The dome will start rotating, then STOP when abort is sent.")

    step("Sending slew command...")
    dome.slew_to_azimuth(target_az)

    step("Waiting 5 seconds for dome to be in motion...")
    time.sleep(5)

    moving = dome.get_slewing()
    if not moving:
        info("Dome finished too quickly — trying a larger slew for abort test...")
        target_az = (dome.get_azimuth() + 270.0) % 360.0
        dome.slew_to_azimuth(target_az)
        time.sleep(5)
        moving = dome.get_slewing()

    info(f"  (get_slewing returned {moving!r})")
    check(moving, "Dome is moving (mid-slew)", "Dome is not moving — cannot test abort")

    pre_abort_az = dome.get_azimuth()

    step("Sending ABORT...")
    dome.abort_slew()

    step("Waiting for dome to stop after abort (up to 15s)...")
    abort_timeout = 15
    start = time.time()
    while time.time() - start < abort_timeout:
        slewing = dome.get_slewing()
        elapsed = int(time.time() - start)
        info(f"  ({elapsed}s) get_slewing={slewing!r}")
        if not slewing:
            break
        time.sleep(POLL_INTERVAL)
    else:
        slewing = dome.get_slewing()

    info(f"Final get_slewing value: {slewing!r}")
    check(not slewing, "Dome has stopped after abort", f"Dome still moving after abort (slewing={slewing!r})")

    post_abort_az = dome.get_azimuth()
    info(f"Pre-abort azimuth:  {pre_abort_az:.1f}°")
    info(f"Post-abort azimuth: {post_abort_az:.1f}°")

    # Verify it didn't reach the target
    dist_to_target = abs(post_abort_az - target_az)
    if dist_to_target > 180:
        dist_to_target = 360 - dist_to_target
    info(f"Distance from target: {dist_to_target:.1f}°")

    observe("Confirm the dome has stopped rotating.")


def test_slaving():
    """Enable and disable dome slaving flag."""
    header("TEST: Dome Slaving Flag")
    ensure_connected()

    step("Enabling slaving...")
    dome.set_slaved(True)
    check(dome.get_slaved() is True, "Slaved flag is True", "Slaved flag did not set to True")

    step("Disabling slaving...")
    dome.set_slaved(False)
    check(dome.get_slaved() is False, "Slaved flag is False", "Slaved flag did not set to False")

    info("(Note: slaving flag controls automatic dome-telescope sync in the server)")


def test_disconnect():
    """Disconnect and verify reads fail when disconnected."""
    header("TEST: Disconnect")
    ensure_connected()

    step("Disconnecting dome...")
    dome.set_connected(False)

    connected = dome.get_connected()
    check(connected is False, "Dome reports disconnected", "Still connected after disconnect")

    step("Verifying azimuth read fails when disconnected...")
    try:
        dome.get_azimuth()
        raise TestFailure("get_azimuth() succeeded while disconnected — should have failed!")
    except RuntimeError as e:
        passed(f"Read correctly rejected while disconnected: {e}")

    step("Reconnecting for cleanup...")
    dome.set_connected(True)
    check(dome.get_connected() is True, "Reconnected successfully", "Failed to reconnect")


# ── Test Registry ────────────────────────────────────────────────────
# (name, function, safe)
#   safe=True  → read-only, no hardware movement, continue on failure
#   safe=False → commands hardware movement, stop on failure

TESTS = [
    ("connect",            test_connect,            True),
    ("read_status",        test_read_status,        True),
    ("capabilities",       test_capabilities,       True),
    ("find_home",          test_find_home,           False),
    ("slew_azimuth",       test_slew_azimuth,       False),
    ("slew_second_target", test_slew_second_target, False),
    ("abort_slew",         test_abort_slew,         False),
    ("slaving",            test_slaving,            True),
    ("disconnect",         test_disconnect,         True),
]


# ── Runner ───────────────────────────────────────────────────────────

def run_tests(selected, simulate=False):
    total = len(selected)
    pass_count = 0
    fail_count = 0
    skipped = 0

    mode = "SIMULATION" if simulate else "HARDWARE"

    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print("║         DOME HARDWARE TEST SUITE                        ║")
    print(f"║         Mode: {mode:10s}   Tests: {total:<3d}                 ║")
    print("║                                                         ║")
    if simulate:
        print("║  SIMULATION MODE — all tests run independently.         ║")
        print("║  Failures will not stop execution.                      ║")
    else:
        print("║  HARDWARE MODE                                          ║")
        print("║  Read-only tests continue on failure.                   ║")
        print("║  Movement tests stop on first failure (safety).         ║")
        print("║                                                         ║")
        print("║  Make sure:                                             ║")
        print("║   - ASCOM Alpaca server is running on :11111            ║")
        print("║   - You can see the dome from where you are             ║")
        print("║   - Nobody is standing near the dome edge               ║")
        print("║   - The dome controller (192.168.1.40) is responding    ║")
    print("╚══════════════════════════════════════════════════════════╝")

    movement_halted = False

    for name, fn, safe in selected:
        if movement_halted and not safe and not simulate:
            print(f"\n  [SKIP] '{name}' — skipped (movement tests halted after earlier failure)")
            skipped += 1
            continue

        try:
            fn()
            pass_count += 1
        except (TestFailure, RuntimeError, Exception) as e:
            fail_count += 1
            if isinstance(e, TestFailure):
                print(f"\n  [FAIL] {e}")
            elif isinstance(e, RuntimeError):
                print(f"\n  [ERROR] ASCOM/connection error during '{name}': {e}")
            else:
                print(f"\n  [ERROR] Unexpected error during '{name}': {type(e).__name__}: {e}")

            if simulate:
                print(f"  (continuing — simulation mode)")
            elif safe:
                print(f"  (continuing — read-only test)")
            else:
                print(f"\n  !! Movement test '{name}' FAILED — halting further movement tests. !!")
                movement_halted = True

    print()
    print("=" * 60)
    parts = [f"{pass_count} passed", f"{fail_count} failed"]
    if skipped:
        parts.append(f"{skipped} skipped")
    print(f"  RESULTS:  {', '.join(parts)},  {total} total")
    if fail_count == 0:
        print("  ALL TESTS PASSED")
    elif movement_halted:
        print("  MOVEMENT TESTS HALTED AFTER FAILURE (read-only tests still ran)")
    print("=" * 60)
    print()

    return 1 if fail_count > 0 else 0


def main():
    parser = argparse.ArgumentParser(description="Dome Hardware Test Suite")
    parser.add_argument("--list", action="store_true", help="List available tests")
    parser.add_argument("--test", nargs="+", metavar="NAME", help="Run specific test(s) by name")
    parser.add_argument("--simulate", action="store_true",
                        help="Simulation mode: run all tests independently, don't stop on failure")
    args = parser.parse_args()

    if args.list:
        print("\nAvailable tests (run order):")
        for name, fn, safe in TESTS:
            tag = "read-only" if safe else "movement"
            print(f"  {name:25s} [{tag:9s}]  {fn.__doc__}")
        print()
        return 0

    test_map = {name: (name, fn, safe) for name, fn, safe in TESTS}

    if args.test:
        selected = []
        for name in args.test:
            if name not in test_map:
                print(f"ERROR: Unknown test '{name}'. Use --list to see available tests.")
                return 1
            selected.append(test_map[name])
    else:
        selected = TESTS

    return run_tests(selected, simulate=args.simulate)


if __name__ == "__main__":
    sys.exit(main())
