"""
Dome Slaving Test Suite
=======================
Tests that dome slaving correctly syncs the dome to the telescope position.

The final test ("multi_position") slews the telescope to 20 different positions
and verifies the dome follows correctly each time, using the dome geometry model
to calculate the expected dome azimuth independently.

Usage:
    python test_slaving.py                          # Run all tests
    python test_slaving.py --simulate               # Run against simulated server (no stop on failure)
    python test_slaving.py --list                   # List available tests
    python test_slaving.py --test connect_both      # Run a single test
    python test_slaving.py --test multi_position    # Just the 20-position sweep

In hardware mode (default):
  - Read-only tests continue on failure (safe — nothing moves)
  - Movement tests stop on first failure (hardware safety)

In simulate mode (--simulate):
  - All tests run independently regardless of failure
"""

import argparse
import csv
import os
from pathlib import Path
import sys
import time
from datetime import datetime

# Ensure the project root is on sys.path so `ascom_requests` is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ascom_requests import telescope
from ascom_requests import dome
from tests.ascom_requests import dome_geometry


# ── Config ───────────────────────────────────────────────────────────

DOME_AZ_TOLERANCE = 12.0    # degrees — dome encoder has ~10° error
POLL_INTERVAL = 2           # seconds between polls
SLEW_TIMEOUT = 120          # seconds max wait for telescope slew
DOME_SETTLE_TIMEOUT = 90    # seconds max wait for slave loop to fire + dome to arrive


class TestFailure(Exception):
    pass


# ── Helpers ──────────────────────────────────────────────────────────

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
    print(f"\n  ** OBSERVE: {msg}")


def passed(msg):
    print(f"  [PASS] {msg}")


def check(condition, pass_msg, fail_msg):
    if condition:
        passed(pass_msg)
    else:
        raise TestFailure(fail_msg)


def ensure_both_connected():
    """Connect to both telescope and dome if not already connected."""
    if not telescope.get_connected():
        step("Telescope not connected — connecting first...")
        telescope.set_connected(True)
        check(telescope.get_connected() is True, "Telescope connected", "Failed to connect telescope")
    if not dome.get_connected():
        step("Dome not connected — connecting first...")
        dome.set_connected(True)
        check(dome.get_connected() is True, "Dome connected", "Failed to connect dome")


def wait_telescope_stopped(timeout=SLEW_TIMEOUT):
    start = time.time()
    while time.time() - start < timeout:
        if not telescope.get_slewing():
            return
        elapsed = int(time.time() - start)
        info(f"  telescope slewing... ({elapsed}s)")
        time.sleep(POLL_INTERVAL)
    raise TestFailure(f"Telescope still slewing after {timeout}s")


def wait_dome_slave_settle(timeout=DOME_SETTLE_TIMEOUT):
    """Poll until the dome slave loop fires, the dome finishes moving, and stays stopped.

    Phases:
      1. Wait for the dome to start slewing (slave loop hasn't fired yet).
         If it doesn't start within a grace period, assume it was already
         close enough and the slave loop decided no move was needed.
      2. Once slewing, poll until it stops.
      3. After it stops, watch for one more cycle in case the slave loop
         re-fires (it may correct after re-reading the telescope position).
    """
    step("Waiting for dome slave to sync...")
    start = time.time()
    stable_since = None

    while True:
        elapsed = time.time() - start
        if elapsed > timeout:
            raise TestFailure(f"Dome did not settle within {timeout}s")

        slewing = dome.get_slewing()
        az = dome.get_azimuth()
        secs = int(elapsed)

        if slewing:
            stable_since = None
            info(f"  dome moving... ({secs}s)  Az={az:.1f}°")
        else:
            if stable_since is None:
                stable_since = time.time()
                info(f"  dome stopped at Az={az:.1f}° ({secs}s) — confirming stable...")
            else:
                idle = time.time() - stable_since
                if idle >= 5:
                    info(f"  dome stable for {idle:.0f}s at Az={az:.1f}°")
                    return

        time.sleep(POLL_INTERVAL)


def pier_side_str(val):
    """Convert pier side int (0=east, 1=west, -1=unknown) to string."""
    if val == 0:
        return "east"
    elif val == 1:
        return "west"
    return "unknown"


def verify_dome_alignment(position_label):
    """
    Read current telescope and dome state, compute expected dome azimuth
    using the geometry model, and compare against actual dome azimuth.
    Returns (expected_az, actual_az, error).
    """
    ra = telescope.get_rightascension()
    dec = telescope.get_declination()
    lst = telescope.get_siderealtime()
    pier = telescope.get_sideofpier()
    dome_az = dome.get_azimuth()

    expected_az = dome_geometry.expected_dome_azimuth(ra, dec, lst, pier)
    error = dome_geometry.azimuth_difference(expected_az, dome_az)

    info(f"Telescope:    RA={ra:.4f}h  Dec={dec:.4f}°  LST={lst:.4f}h  Pier={pier_side_str(pier)}")
    info(f"Dome actual:  {dome_az:.1f}°")
    info(f"Dome expect:  {expected_az:.1f}°")
    info(f"Error:        {error:.1f}° (tolerance: {DOME_AZ_TOLERANCE}°)")

    check(
        error < DOME_AZ_TOLERANCE,
        f"{position_label}: dome aligned (error {error:.1f}° < {DOME_AZ_TOLERANCE}°)",
        f"{position_label}: dome NOT aligned — error {error:.1f}° exceeds {DOME_AZ_TOLERANCE}° tolerance",
    )

    return expected_az, dome_az, error


# ── Tests ────────────────────────────────────────────────────────────

def test_connect_both():
    """Connect to both telescope and dome."""
    header("TEST: Connect Telescope and Dome")

    step("Connecting telescope...")
    telescope.set_connected(True)
    check(telescope.get_connected() is True, "Telescope connected", "Telescope failed to connect")

    step("Connecting dome...")
    dome.set_connected(True)
    check(dome.get_connected() is True, "Dome connected", "Dome failed to connect")

    name_t = telescope.get_name()
    name_d = dome.get_name()
    info(f"Telescope: {name_t}")
    info(f"Dome:      {name_d}")


def test_enable_slaving():
    """Enable dome slaving and verify the flag is set."""
    header("TEST: Enable Dome Slaving")
    ensure_both_connected()

    # Make sure telescope is unparked and tracking
    if telescope.get_atpark():
        step("Telescope is parked — unparking...")
        telescope.unpark()

    step("Enabling tracking...")
    telescope.set_tracking(True)
    check(telescope.get_tracking() is True, "Tracking enabled", "Failed to enable tracking")

    step("Enabling dome slaving...")
    dome.set_slaved(True)
    check(dome.get_slaved() is True, "Dome slaving enabled", "Failed to enable slaving")

    observe("Dome slaving is now ON. The server will automatically sync the dome to the telescope.")


def test_initial_sync():
    """Verify the dome syncs to the telescope's current position."""
    header("TEST: Initial Slave Sync (no slew — just check current alignment)")
    ensure_both_connected()

    observe("The dome should already be moving to match the telescope's current pointing.")

    wait_dome_slave_settle()

    step("Checking alignment...")
    verify_dome_alignment("Initial sync")

    observe("Confirm the dome slit is roughly aligned with the telescope tube direction.")


def test_single_slew_sync():
    """Slew the telescope and verify the dome follows."""
    header("TEST: Single Slew — Dome Follows Telescope")
    ensure_both_connected()

    cur_ra = telescope.get_rightascension()
    cur_dec = telescope.get_declination()

    target_ra = (cur_ra + 1.5) % 24.0
    target_dec = max(min(cur_dec + 10.0, -10.0), -80.0)

    info(f"Current:  RA={cur_ra:.4f}h  Dec={cur_dec:.4f}°")
    info(f"Target:   RA={target_ra:.4f}h  Dec={target_dec:.4f}°")

    observe(f"Telescope will slew to RA={target_ra:.2f}h Dec={target_dec:.2f}°. Watch both telescope AND dome.")

    step("Slewing telescope...")
    telescope.slew_to_coordinates_async(target_ra, target_dec)
    time.sleep(1)
    check(telescope.get_slewing() is True, "Telescope is slewing", "Telescope did not start slewing")

    step("Waiting for telescope to arrive...")
    wait_telescope_stopped()

    step("Waiting for dome slave to catch up...")
    wait_dome_slave_settle()

    step("Checking alignment after slew...")
    verify_dome_alignment("Post-slew sync")

    observe("Confirm both the telescope and dome slit are pointing in the same direction.")


def test_disable_slaving():
    """Disable slaving and verify the dome stops following."""
    header("TEST: Disable Slaving")
    ensure_both_connected()

    step("Disabling dome slaving...")
    dome.set_slaved(False)
    check(dome.get_slaved() is False, "Dome slaving disabled", "Failed to disable slaving")

    dome_az_before = dome.get_azimuth()

    # Slew telescope — dome should NOT follow
    cur_ra = telescope.get_rightascension()
    target_ra = (cur_ra + 2.0) % 24.0
    cur_dec = telescope.get_declination()
    target_dec = max(min(cur_dec - 10.0, -10.0), -80.0)

    step(f"Slewing telescope to RA={target_ra:.2f}h Dec={target_dec:.2f}° with slaving OFF...")
    telescope.slew_to_coordinates_async(target_ra, target_dec)
    wait_telescope_stopped()

    # Wait a bit to be sure the dome doesn't move
    time.sleep(10)

    dome_az_after = dome.get_azimuth()
    dome_moved = dome_geometry.azimuth_difference(dome_az_before, dome_az_after)

    info(f"Dome azimuth before slew: {dome_az_before:.1f}°")
    info(f"Dome azimuth after slew:  {dome_az_after:.1f}°")
    info(f"Dome movement: {dome_moved:.1f}°")

    check(dome_moved < 5.0, f"Dome stayed put with slaving off (moved {dome_moved:.1f}°)", f"Dome moved {dome_moved:.1f}° with slaving off — should not have moved!")

    observe("The dome should NOT have moved during that telescope slew.")

    # Re-enable for the multi-position test
    step("Re-enabling dome slaving for subsequent tests...")
    dome.set_slaved(True)
    check(dome.get_slaved() is True, "Dome slaving re-enabled", "Failed to re-enable slaving")


def test_multi_position():
    """Slew telescope to 20 positions and verify dome follows each time."""
    header("TEST: Multi-Position Alignment Sweep (20 positions)")
    ensure_both_connected()

    observe("This test will slew the telescope to 20 different positions across the sky.")
    observe("After each slew, it verifies the dome has followed correctly.")
    observe("Watch both the telescope and dome throughout — this will take a while.")

    # Make sure slaving is on
    if not dome.get_slaved():
        step("Enabling dome slaving...")
        dome.set_slaved(True)

    if telescope.get_atpark():
        step("Unparking telescope...")
        telescope.unpark()
    telescope.set_tracking(True)

    # Generate 20 target positions spread across the visible sky.
    # For Sydney (lat -33.9), all targets use Dec ≤ -20° and RA within 3h
    # of LST, keeping everything well above the horizon (min alt ~34°).
    lst = telescope.get_siderealtime()
    info(f"Current LST: {lst:.4f}h")

    targets = []
    # 4 Dec bands x 5 RA offsets = 20 positions
    dec_values = [-70.0, -50.0, -30.0, -20.0]
    ra_offsets = [-3.0, -1.5, 0.0, 1.5, 3.0]  # hours from LST

    for dec in dec_values:
        for ra_off in ra_offsets:
            ra = (lst + ra_off) % 24.0
            targets.append((ra, dec))

    total = len(targets)
    pass_count = 0
    errors = []

    # ── Set up log file ──────────────────────────────────────────────

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"slaving_test_{timestamp}.csv")
    log_txt_path = os.path.join(log_dir, f"slaving_test_{timestamp}.txt")

    csv_file = open(log_path, "w", newline="")
    csv_writer = csv.writer(csv_file)
    csv_writer.writerow([
        "#",
        "Target RA (h)", "Target Dec (°)",
        "Actual RA (h)", "Actual Dec (°)",
        "LST (h)", "Pier Side",
        "Telescope Alt (°)", "Telescope Az (°)",
        "Expected Dome Az (°)", "Actual Dome Az (°)",
        "Az Error (°)", f"Tolerance (°)",
        "Result",
    ])

    # Also write a human-readable text log
    txt_file = open(log_txt_path, "w")
    txt_file.write("=" * 72 + "\n")
    txt_file.write("  DOME SLAVING MULTI-POSITION TEST LOG\n")
    txt_file.write(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    txt_file.write(f"  Initial LST: {lst:.4f}h\n")
    txt_file.write(f"  Tolerance: {DOME_AZ_TOLERANCE}°\n")
    txt_file.write(f"  Site: lat={dome_geometry.SITE_LATITUDE}° lon={dome_geometry.SITE_LONGITUDE}°\n")
    txt_file.write(f"  Dome radius: {dome_geometry.DOME_RADIUS}m\n")
    txt_file.write(f"  Mount offsets: NS={dome_geometry.MOUNT_OFFSET_NS}m EW={dome_geometry.MOUNT_OFFSET_EW}m\n")
    txt_file.write(f"  Pier height: {dome_geometry.MOUNT_PIER_HEIGHT}m\n")
    txt_file.write(f"  RA→DEC: {dome_geometry.POLAR_AXIS_TO_DEC_AXIS}m  DEC→scope: {dome_geometry.DEC_AXIS_TO_TELESCOPE}m\n")
    txt_file.write(f"  Tube radius: {dome_geometry.TELESCOPE_RADIUS}m\n")
    txt_file.write("=" * 72 + "\n\n")

    info(f"Logging results to:")
    info(f"  CSV: {log_path}")
    info(f"  TXT: {log_txt_path}")

    # ── Run positions ────────────────────────────────────────────────

    print(f"\n  {'─' * 56}")
    print(f"  {'#':>3}  {'RA (h)':>8}  {'Dec (°)':>8}  {'Expect':>8}  {'Actual':>8}  {'Err':>6}  Result")
    print(f"  {'─' * 56}")

    for i, (target_ra, target_dec) in enumerate(targets, 1):
        position_label = f"Position {i}/{total}"

        print(f"\n  ┌──────────────────────────────────────────────────┐")
        print(f"  │  {position_label}: RA={target_ra:.2f}h  Dec={target_dec:.1f}°{' ' * 14}│")
        print(f"  └──────────────────────────────────────────────────┘")

        observe(f"Telescope slewing to RA={target_ra:.2f}h Dec={target_dec:.1f}°. Watch it move.")

        step("Slewing telescope...")
        telescope.slew_to_coordinates_async(target_ra, target_dec)
        time.sleep(1)

        step("Waiting for telescope to arrive...")
        wait_telescope_stopped()

        step("Waiting for dome to follow...")
        wait_dome_slave_settle()

        # Read all state for logging
        actual_ra = telescope.get_rightascension()
        actual_dec = telescope.get_declination()
        lst_now = telescope.get_siderealtime()
        pier = telescope.get_sideofpier()
        tel_alt = telescope.get_altitude()
        tel_az = telescope.get_azimuth()
        actual_dome_az = dome.get_azimuth()
        expected_az = dome_geometry.expected_dome_azimuth(actual_ra, actual_dec, lst_now, pier)
        az_error = dome_geometry.azimuth_difference(expected_az, actual_dome_az)

        step("Verifying alignment...")
        info(f"Telescope:    RA={actual_ra:.4f}h  Dec={actual_dec:.4f}°  Alt={tel_alt:.2f}°  Az={tel_az:.2f}°")
        info(f"              LST={lst_now:.4f}h  Pier={pier_side_str(pier)}")
        info(f"Dome actual:  {actual_dome_az:.1f}°")
        info(f"Dome expect:  {expected_az:.1f}°")
        info(f"Error:        {az_error:.1f}° (tolerance: {DOME_AZ_TOLERANCE}°)")

        if az_error < DOME_AZ_TOLERANCE:
            status = "PASS"
            pass_count += 1
            passed(f"{position_label}: dome aligned (error {az_error:.1f}° < {DOME_AZ_TOLERANCE}°)")
        else:
            status = "FAIL"
            errors.append((i, target_ra, target_dec, expected_az, actual_dome_az, az_error))
            print(f"  [FAIL] {position_label}: dome NOT aligned — error {az_error:.1f}° exceeds {DOME_AZ_TOLERANCE}°")

        print(f"  {i:3d}  {target_ra:8.2f}  {target_dec:8.1f}  {expected_az:8.1f}  {actual_dome_az:8.1f}  {az_error:5.1f}°  {status}")

        # ── Write to log files ───────────────────────────────────────

        csv_writer.writerow([
            i,
            f"{target_ra:.4f}", f"{target_dec:.1f}",
            f"{actual_ra:.4f}", f"{actual_dec:.4f}",
            f"{lst_now:.4f}", pier_side_str(pier),
            f"{tel_alt:.2f}", f"{tel_az:.2f}",
            f"{expected_az:.2f}", f"{actual_dome_az:.2f}",
            f"{az_error:.2f}", f"{DOME_AZ_TOLERANCE}",
            status,
        ])
        csv_file.flush()

        txt_file.write(f"Position {i}/{total}\n")
        txt_file.write(f"  Target:      RA={target_ra:.4f}h  Dec={target_dec:.1f}°\n")
        txt_file.write(f"  Telescope:   RA={actual_ra:.4f}h  Dec={actual_dec:.4f}°  Alt={tel_alt:.2f}°  Az={tel_az:.2f}°\n")
        txt_file.write(f"  Sidereal:    LST={lst_now:.4f}h  Pier side={pier_side_str(pier)}\n")
        txt_file.write(f"  Dome:        expected={expected_az:.2f}°  actual={actual_dome_az:.2f}°\n")
        txt_file.write(f"  Error:       {az_error:.2f}° (tolerance: {DOME_AZ_TOLERANCE}°)\n")
        txt_file.write(f"  Result:      {status}\n")
        txt_file.write(f"{'─' * 72}\n")
        txt_file.flush()

    # ── Summary ──────────────────────────────────────────────────────

    summary_lines = []
    summary_lines.append(f"\n  {'─' * 56}")
    summary_lines.append(f"\n  Multi-Position Summary:")
    summary_lines.append(f"    Passed: {pass_count}/{total}")
    summary_lines.append(f"    Failed: {total - pass_count}/{total}")

    if errors:
        summary_lines.append(f"\n  Failed positions:")
        for idx, ra, dec_val, exp, act, err in errors:
            summary_lines.append(f"    #{idx}: RA={ra:.2f}h Dec={dec_val:.1f}° — expected {exp:.1f}°, got {act:.1f}° (err {err:.1f}°)")

    for line in summary_lines:
        print(line)

    # Write summary to text log
    txt_file.write("\n" + "=" * 72 + "\n")
    txt_file.write("  SUMMARY\n")
    txt_file.write("=" * 72 + "\n")
    txt_file.write(f"  Passed: {pass_count}/{total}\n")
    txt_file.write(f"  Failed: {total - pass_count}/{total}\n")
    if errors:
        txt_file.write(f"\n  Failed positions:\n")
        for idx, ra, dec_val, exp, act, err in errors:
            txt_file.write(f"    #{idx}: RA={ra:.2f}h Dec={dec_val:.1f}° — expected {exp:.1f}°, got {act:.1f}° (err {err:.1f}°)\n")
    txt_file.write("\n")
    txt_file.close()
    csv_file.close()

    info(f"Results saved to:")
    info(f"  CSV: {log_path}")
    info(f"  TXT: {log_txt_path}")

    check(
        pass_count == total,
        f"All {total} positions aligned within tolerance",
        f"{total - pass_count} of {total} positions failed alignment check (see above)",
    )


def test_cleanup():
    """Disable slaving, park telescope, disconnect both devices."""
    header("TEST: Cleanup")
    ensure_both_connected()

    step("Disabling dome slaving...")
    dome.set_slaved(False)

    step("Parking telescope...")
    telescope.park()
    wait_telescope_stopped()

    observe("Telescope should now be parked.")

    step("Disconnecting telescope...")
    telescope.set_connected(False)
    check(telescope.get_connected() is False, "Telescope disconnected", "Telescope still connected")

    step("Disconnecting dome...")
    dome.set_connected(False)
    check(dome.get_connected() is False, "Dome disconnected", "Dome still connected")

    passed("Cleanup complete — both devices disconnected, telescope parked")


# ── Test Registry ────────────────────────────────────────────────────
# (name, function, safe)
#   safe=True  → read-only, no hardware movement, continue on failure
#   safe=False → commands hardware movement, stop on failure

TESTS = [
    ("connect_both",     test_connect_both,     True),
    ("enable_slaving",   test_enable_slaving,   False),
    ("initial_sync",     test_initial_sync,     False),
    ("single_slew_sync", test_single_slew_sync, False),
    ("disable_slaving",  test_disable_slaving,  False),
    ("multi_position",   test_multi_position,   False),
    ("cleanup",          test_cleanup,           False),
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
    print("║         DOME SLAVING TEST SUITE                         ║")
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
        print("║   - You can see BOTH telescope and dome                 ║")
        print("║   - Nothing is in the telescope's swing path            ║")
        print("║   - The dome area is clear of people                    ║")
        print("║   - You have the mount keypad for emergency stop        ║")
        print("║                                                         ║")
        print("║  The multi_position test slews to 20 sky positions.     ║")
        print("║  This will take 15-30 minutes depending on hardware.    ║")
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
    parser = argparse.ArgumentParser(description="Dome Slaving Test Suite")
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
