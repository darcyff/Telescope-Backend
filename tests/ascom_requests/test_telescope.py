"""
Telescope Hardware Test Suite
=============================
Tests core telescope functionality against the ASCOM Alpaca server.
Designed to be run at the observatory with eyes on the hardware.

Usage:
    python test_telescope.py                       # Run all tests in order
    python test_telescope.py --simulate            # Run against simulated server (no stop on failure)
    python test_telescope.py --list                # List available tests
    python test_telescope.py --test connect        # Run a single test
    python test_telescope.py --test connect read_status tracking

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

from ascom_requests import telescope


# ── Helpers ──────────────────────────────────────────────────────────

RA_TOLERANCE = 0.02       # hours (~0.3 degrees)
DEC_TOLERANCE = 0.5       # degrees
ALTAZ_TOLERANCE = 1.0     # degrees
POLL_INTERVAL = 2         # seconds between slewing polls
SLEW_TIMEOUT = 120        # seconds max wait for a slew


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


def wait_until_stopped(timeout=SLEW_TIMEOUT):
    """Poll slewing status until the telescope stops or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        if not telescope.get_slewing():
            return True
        elapsed = int(time.time() - start)
        ra = telescope.get_rightascension()
        dec = telescope.get_declination()
        info(f"  slewing... ({elapsed}s)  RA={ra:.4f}h  Dec={dec:.4f}°")
        time.sleep(POLL_INTERVAL)
    raise TestFailure(f"Telescope still slewing after {timeout}s — possible hang. Aborting.")


# ── Tests ────────────────────────────────────────────────────────────

def test_connect():
    """Connect to the telescope and verify the connection."""
    header("TEST: Connect to Telescope")

    step("Connecting...")
    telescope.set_connected(True)

    connected = telescope.get_connected()
    check(connected is True, "Connected successfully", "set_connected(True) did not connect")

    name = telescope.get_name()
    info(f"Telescope name: {name}")
    check(name is not None and len(str(name)) > 0, f"Got telescope name: {name}", "Telescope name is empty")


def test_read_status():
    """Read all basic status properties — nothing moves, read-only."""
    header("TEST: Read Basic Status (read-only, nothing moves)")

    props = {
        "Name":             telescope.get_name,
        "Description":      telescope.get_description,
        "Driver Info":      telescope.get_driverinfo,
        "Driver Version":   telescope.get_driverversion,
        "Interface Ver":    telescope.get_interfaceversion,
        "Right Ascension":  telescope.get_rightascension,
        "Declination":      telescope.get_declination,
        "Altitude":         telescope.get_altitude,
        "Azimuth":          telescope.get_azimuth,
        "Sidereal Time":    telescope.get_siderealtime,
        "Side of Pier":     telescope.get_sideofpier,
        "Tracking":         telescope.get_tracking,
        "At Park":          telescope.get_atpark,
        "At Home":          telescope.get_athome,
        "Slewing":          telescope.get_slewing,
    }

    for label, fn in props.items():
        val = fn()
        info(f"{label:20s} = {val}")
        check(val is not None, f"{label} returned a value", f"{label} returned None")

    observe("Cross-check the RA/Dec/Alt/Az values above against the mount keypad display.")

    # Verify position values are in valid ranges
    ra = telescope.get_rightascension()
    dec = telescope.get_declination()
    alt = telescope.get_altitude()
    az = telescope.get_azimuth()

    check(0 <= ra < 24, f"RA in valid range [0,24): {ra:.4f}h", f"RA out of range: {ra}")
    check(-90 <= dec <= 90, f"Dec in valid range [-90,90]: {dec:.4f}°", f"Dec out of range: {dec}")
    check(-90 <= alt <= 90, f"Alt in valid range [-90,90]: {alt:.4f}°", f"Alt out of range: {alt}")
    check(0 <= az < 360, f"Az in valid range [0,360): {az:.4f}°", f"Az out of range: {az}")


def test_tracking():
    """Enable and disable sidereal tracking."""
    header("TEST: Tracking Control")

    # Make sure we're unparked first
    if telescope.get_atpark():
        step("Telescope is parked — unparking first...")
        telescope.unpark()

    step("Enabling tracking...")
    telescope.set_tracking(True)
    check(telescope.get_tracking() is True, "Tracking is ON", "Tracking did not enable")

    observe("The telescope should now be tracking (motors compensating for Earth rotation).")

    rate = telescope.get_trackingrate()
    info(f"Tracking rate: {rate} (0=Sidereal, 1=Lunar, 2=Solar)")
    check(rate == 0, "Tracking rate is Sidereal (0)", f"Unexpected tracking rate: {rate}")

    rates = telescope.get_trackingrates()
    info(f"Supported tracking rates: {rates}")
    check(isinstance(rates, list) and len(rates) > 0, "Got supported tracking rates", "No tracking rates returned")

    step("Disabling tracking...")
    telescope.set_tracking(False)
    check(telescope.get_tracking() is False, "Tracking is OFF", "Tracking did not disable")

    # Re-enable for subsequent tests
    step("Re-enabling tracking for subsequent tests...")
    telescope.set_tracking(True)
    check(telescope.get_tracking() is True, "Tracking re-enabled", "Failed to re-enable tracking")


def test_slew_coordinates():
    """Slew to RA/Dec coordinates and verify arrival."""
    header("TEST: Slew to Equatorial Coordinates (async)")

    if telescope.get_atpark():
        step("Telescope is parked — unparking first...")
        telescope.unpark()
        telescope.set_tracking(True)

    # Read current position and compute a safe offset target
    cur_ra = telescope.get_rightascension()
    cur_dec = telescope.get_declination()
    info(f"Current position:  RA={cur_ra:.4f}h  Dec={cur_dec:.4f}°")

    # Move +1h RA, and clamp Dec to a safe range
    target_ra = (cur_ra + 1.0) % 24.0
    target_dec = max(min(cur_dec + 5.0, 10.0), -80.0)
    info(f"Target position:   RA={target_ra:.4f}h  Dec={target_dec:.4f}°")

    observe(f"The telescope is about to slew. Watch it move toward RA={target_ra:.2f}h Dec={target_dec:.2f}°.")

    step("Sending slew command...")
    telescope.slew_to_coordinates_async(target_ra, target_dec)

    # Confirm it's slewing
    time.sleep(1)
    slewing = telescope.get_slewing()
    check(slewing is True, "Telescope is slewing", "Telescope did not start slewing")
    observe("The telescope should be physically moving now. Confirm it is slewing.")

    step("Waiting for slew to complete...")
    wait_until_stopped()

    final_ra = telescope.get_rightascension()
    final_dec = telescope.get_declination()
    info(f"Final position:    RA={final_ra:.4f}h  Dec={final_dec:.4f}°")
    info(f"Target was:        RA={target_ra:.4f}h  Dec={target_dec:.4f}°")

    ra_err = abs(final_ra - target_ra)
    if ra_err > 12:
        ra_err = 24 - ra_err  # handle wraparound
    dec_err = abs(final_dec - target_dec)
    info(f"Error:             RA={ra_err:.4f}h  Dec={dec_err:.4f}°")

    check(ra_err < RA_TOLERANCE, f"RA within tolerance ({ra_err:.4f}h < {RA_TOLERANCE}h)", f"RA error too large: {ra_err:.4f}h")
    check(dec_err < DEC_TOLERANCE, f"Dec within tolerance ({dec_err:.4f}° < {DEC_TOLERANCE}°)", f"Dec error too large: {dec_err:.4f}°")

    observe("Confirm the telescope has stopped and is pointing at the new position.")


def test_slew_altaz():
    """Slew to Alt/Az coordinates and verify arrival."""
    header("TEST: Slew to Alt/Az Coordinates (async)")

    if telescope.get_atpark():
        step("Telescope is parked — unparking first...")
        telescope.unpark()
        telescope.set_tracking(True)

    cur_alt = telescope.get_altitude()
    cur_az = telescope.get_azimuth()
    info(f"Current position:  Alt={cur_alt:.4f}°  Az={cur_az:.4f}°")

    # Pick a safe target: moderate altitude, offset azimuth by 30 degrees
    target_alt = 45.0
    target_az = (cur_az + 30.0) % 360.0
    info(f"Target position:   Alt={target_alt:.4f}°  Az={target_az:.4f}°")

    observe(f"The telescope is about to slew to Alt={target_alt:.1f}° Az={target_az:.1f}°. Watch it move.")

    step("Sending alt/az slew command...")
    telescope.slew_to_altaz_async(target_az, target_alt)

    time.sleep(1)
    slewing = telescope.get_slewing()
    check(slewing is True, "Telescope is slewing", "Telescope did not start slewing")

    step("Waiting for slew to complete...")
    wait_until_stopped()

    final_alt = telescope.get_altitude()
    final_az = telescope.get_azimuth()
    info(f"Final position:    Alt={final_alt:.4f}°  Az={final_az:.4f}°")

    alt_err = abs(final_alt - target_alt)
    az_err = abs(final_az - target_az)
    if az_err > 180:
        az_err = 360 - az_err
    info(f"Error:             Alt={alt_err:.4f}°  Az={az_err:.4f}°")

    check(alt_err < ALTAZ_TOLERANCE, f"Alt within tolerance ({alt_err:.4f}° < {ALTAZ_TOLERANCE}°)", f"Alt error too large: {alt_err:.4f}°")
    check(az_err < ALTAZ_TOLERANCE, f"Az within tolerance ({az_err:.4f}° < {ALTAZ_TOLERANCE}°)", f"Az error too large: {az_err:.4f}°")


def test_abort_slew():
    """Start a slew and then abort it. Verify the telescope stops."""
    header("TEST: Abort Slew")

    if telescope.get_atpark():
        step("Telescope is parked — unparking first...")
        telescope.unpark()
        telescope.set_tracking(True)

    cur_ra = telescope.get_rightascension()
    cur_dec = telescope.get_declination()

    # Pick a target far enough that we have time to abort
    target_ra = (cur_ra + 3.0) % 24.0
    target_dec = max(min(cur_dec - 10.0, 10.0), -80.0)
    info(f"Slewing to distant target RA={target_ra:.4f}h Dec={target_dec:.4f}° ...")

    observe("The telescope will start slewing, then STOP when abort is sent.")

    step("Sending slew command...")
    telescope.slew_to_coordinates_async(target_ra, target_dec)

    step("Waiting 3 seconds for slew to be in progress...")
    time.sleep(3)
    check(telescope.get_slewing() is True, "Telescope is slewing (mid-slew)", "Telescope did not start slewing")

    step("Sending ABORT...")
    telescope.abort_slew()
    time.sleep(2)

    slewing = telescope.get_slewing()
    check(slewing is False, "Telescope has stopped after abort", "Telescope still slewing after abort!")

    observe("Confirm the telescope has stopped moving.")

    # Verify it did NOT reach the target
    final_ra = telescope.get_rightascension()
    ra_diff = abs(final_ra - target_ra)
    if ra_diff > 12:
        ra_diff = 24 - ra_diff
    info(f"Distance from target after abort: {ra_diff:.4f}h RA")
    check(ra_diff > RA_TOLERANCE, "Telescope stopped before reaching target (abort worked)", "Telescope reached target despite abort — test inconclusive")


def test_park_unpark():
    """Park the telescope, verify slews are rejected, then unpark."""
    header("TEST: Park and Unpark")

    if telescope.get_atpark():
        step("Already parked — unparking to start fresh...")
        telescope.unpark()

    telescope.set_tracking(True)

    observe("The telescope is about to PARK. It will slew to its park position.")

    step("Parking telescope...")
    telescope.park()

    # Wait for park slew to complete
    step("Waiting for park to complete...")
    wait_until_stopped()

    check(telescope.get_atpark() is True, "Telescope reports at park", "atpark is not True after parking")
    observe("Confirm the telescope has parked (usually pointed at pole or zenith).")

    step("Verifying slew is rejected while parked...")
    try:
        telescope.slew_to_coordinates_async(12.0, -30.0)
        raise TestFailure("Slew was accepted while parked — should have been rejected!")
    except RuntimeError as e:
        passed(f"Slew correctly rejected while parked: {e}")

    step("Unparking telescope...")
    telescope.unpark()
    check(telescope.get_atpark() is False, "Telescope reports not parked after unpark", "atpark still True after unpark")

    step("Verifying slew works after unpark...")
    telescope.set_tracking(True)
    cur_ra = telescope.get_rightascension()
    target_ra = (cur_ra + 0.5) % 24.0
    cur_dec = telescope.get_declination()
    target_dec = max(min(cur_dec, 10.0), -80.0)
    telescope.slew_to_coordinates_async(target_ra, target_dec)
    time.sleep(1)
    slewing = telescope.get_slewing()
    check(slewing is True, "Telescope accepted slew after unpark", "Telescope did not slew after unpark")
    step("Waiting for post-unpark slew to complete...")
    wait_until_stopped()
    passed("Post-unpark slew completed successfully")


def test_target_coordinates():
    """Set and read target coordinates, then slew to target."""
    header("TEST: Target Coordinates and SlewToTarget")

    if telescope.get_atpark():
        step("Telescope is parked — unparking first...")
        telescope.unpark()
        telescope.set_tracking(True)

    cur_ra = telescope.get_rightascension()
    cur_dec = telescope.get_declination()

    target_ra = (cur_ra + 0.5) % 24.0
    target_dec = max(min(cur_dec + 3.0, 10.0), -80.0)

    step(f"Setting target RA={target_ra:.4f}h Dec={target_dec:.4f}°...")
    telescope.set_targetrightascension(target_ra)
    telescope.set_targetdeclination(target_dec)

    read_ra = telescope.get_targetrightascension()
    read_dec = telescope.get_targetdeclination()
    info(f"Read back target:  RA={read_ra:.4f}h  Dec={read_dec:.4f}°")

    check(abs(read_ra - target_ra) < 0.001, "Target RA matches", f"Target RA mismatch: set {target_ra}, got {read_ra}")
    check(abs(read_dec - target_dec) < 0.01, "Target Dec matches", f"Target Dec mismatch: set {target_dec}, got {read_dec}")

    observe(f"Telescope will slew to target RA={target_ra:.2f}h Dec={target_dec:.2f}°.")

    step("Sending slewtotarget...")
    telescope.slew_to_target_async()
    time.sleep(1)
    check(telescope.get_slewing() is True, "Telescope is slewing to target", "Telescope did not start slewing to target")

    step("Waiting for slew to complete...")
    wait_until_stopped()

    final_ra = telescope.get_rightascension()
    final_dec = telescope.get_declination()
    ra_err = abs(final_ra - target_ra)
    if ra_err > 12:
        ra_err = 24 - ra_err
    dec_err = abs(final_dec - target_dec)
    info(f"Final position:    RA={final_ra:.4f}h  Dec={final_dec:.4f}°  (err RA={ra_err:.4f}h Dec={dec_err:.4f}°)")

    check(ra_err < RA_TOLERANCE, "RA at target within tolerance", f"RA error too large: {ra_err:.4f}h")
    check(dec_err < DEC_TOLERANCE, "Dec at target within tolerance", f"Dec error too large: {dec_err:.4f}°")


def test_capabilities():
    """Read all capability flags and verify they return booleans."""
    header("TEST: Capability Flags")

    caps = {
        "CanFindHome":              telescope.get_canfindhome,
        "CanPark":                  telescope.get_canpark,
        "CanPulseGuide":            telescope.get_canpulseguide,
        "CanSetDeclinationRate":    telescope.get_cansetdeclinationrate,
        "CanSetGuideRates":         telescope.get_cansetguiderates,
        "CanSetPark":               telescope.get_cansetpark,
        "CanSetPierSide":           telescope.get_cansetpierside,
        "CanSetRightAscensionRate": telescope.get_cansetrightascensionrate,
        "CanSetTracking":           telescope.get_cansettracking,
        "CanSlew":                  telescope.get_canslew,
        "CanSlewAltAz":             telescope.get_canslewaltaz,
        "CanSlewAltAzAsync":        telescope.get_canslewaltazasync,
        "CanSlewAsync":             telescope.get_canslewasync,
        "CanSync":                  telescope.get_cansync,
        "CanSyncAltAz":             telescope.get_cansyncaltaz,
        "CanUnpark":                telescope.get_canunpark,
        "CanMoveAxis":              telescope.get_canmoveaxis,
    }

    for label, fn in caps.items():
        val = fn()
        info(f"{label:30s} = {val}")
        check(isinstance(val, bool), f"{label} returned bool", f"{label} returned non-bool: {type(val)}")


def test_site_info():
    """Read and verify site location info."""
    header("TEST: Site Information")

    lat = telescope.get_sitelatitude()
    lon = telescope.get_sitelongitude()
    elev = telescope.get_siteelevation()

    info(f"Latitude:  {lat}")
    info(f"Longitude: {lon}")
    info(f"Elevation: {elev}m")

    check(-90 <= lat <= 90, f"Latitude in valid range: {lat}", f"Latitude out of range: {lat}")
    check(-180 <= lon <= 360, f"Longitude in valid range: {lon}", f"Longitude out of range: {lon}")
    check(elev >= 0, f"Elevation non-negative: {elev}m", f"Elevation negative: {elev}")


def test_disconnect():
    """Disconnect and verify reads fail when disconnected."""
    header("TEST: Disconnect")

    step("Disconnecting telescope...")
    telescope.set_connected(False)

    connected = telescope.get_connected()
    check(connected is False, "Telescope reports disconnected", "Still connected after disconnect")

    step("Verifying position read fails when disconnected...")
    try:
        telescope.get_rightascension()
        raise TestFailure("get_rightascension() succeeded while disconnected — should have failed!")
    except RuntimeError as e:
        passed(f"Read correctly rejected while disconnected: {e}")

    step("Reconnecting for cleanup...")
    telescope.set_connected(True)
    check(telescope.get_connected() is True, "Reconnected successfully", "Failed to reconnect")


# ── Test Registry ────────────────────────────────────────────────────
# (name, function, safe)
#   safe=True  → read-only, no hardware movement, continue on failure
#   safe=False → commands hardware movement, stop on failure

TESTS = [
    ("connect",            test_connect,            True),
    ("read_status",        test_read_status,        True),
    ("capabilities",       test_capabilities,       True),
    ("site_info",          test_site_info,          True),
    ("tracking",           test_tracking,           False),
    ("slew_coordinates",   test_slew_coordinates,   False),
    ("slew_altaz",         test_slew_altaz,         False),
    ("target_coordinates", test_target_coordinates, False),
    ("abort_slew",         test_abort_slew,         False),
    ("park_unpark",        test_park_unpark,        False),
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
    print("║         TELESCOPE HARDWARE TEST SUITE                   ║")
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
        print("║   - You can see the telescope from where you are        ║")
        print("║   - Nothing is in the telescope's swing path            ║")
        print("║   - You have access to the mount keypad for emergency   ║")
    print("╚══════════════════════════════════════════════════════════╝")

    movement_halted = False

    for name, fn, safe in selected:
        # In hardware mode, if a movement test failed, skip remaining movement tests
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
    parser = argparse.ArgumentParser(description="Telescope Hardware Test Suite")
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
