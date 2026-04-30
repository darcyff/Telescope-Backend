"""
Satellite Visibility Test Suite
================================
Tests for the satellite_visibility module integration.

Tests are split into two groups:
  - Safe (no hardware, no ephemeris): math/conversion checks that always run.
  - Data tests: require the ephemeris CSV file from the satellite tracker.
  - Hardware tests: require ASCOM telescope connection.

Usage:
    python -m tests.ascom_requests.test_satellite_visibility          # all tests
    python -m tests.ascom_requests.test_satellite_visibility --list   # list tests
    python -m tests.ascom_requests.test_satellite_visibility --test math_ra_conversion,math_dec_conversion
    python -m tests.ascom_requests.test_satellite_visibility --skip-hardware
"""

import sys
import os
import argparse
from datetime import datetime

# Allow running as a script from any directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ascom_requests import satellite_visibility as sv


# ── Helpers ─────────────────────────────────────────────────────────────────

class TestFailure(Exception):
    pass


def _header(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def _step(msg: str):
    print(f"  >> {msg}")


def _passed(msg: str = ""):
    print(f"  [PASS]{' ' + msg if msg else ''}")


def _check(condition: bool, msg: str):
    if not condition:
        raise TestFailure(f"FAIL: {msg}")
    _passed(msg)


# ── Math / conversion tests (no dependencies) ────────────────────────────────

def test_math_ra_conversion():
    _header("RA degree -> hours conversion")
    _step("RA 0 deg -> 0h")
    h, m, s = sv.ra_deg_to_hours(0.0)
    _check(h == 0 and m == 0, f"Expected 0h 0m, got {h}h {m}m")

    _step("RA 360 deg -> 24h")
    h, m, s = sv.ra_deg_to_hours(360.0)
    _check(h == 24 and m == 0, f"Expected 24h 0m, got {h}h {m}m")

    _step("RA 180 deg -> 12h")
    h, m, s = sv.ra_deg_to_hours(180.0)
    _check(h == 12 and m == 0, f"Expected 12h 0m, got {h}h {m}m")

    _step("RA decimal hours conversion")
    ra_h = sv.ra_deg_to_hours_decimal(90.0)
    _check(abs(ra_h - 6.0) < 1e-9, f"Expected 6.0h, got {ra_h}")

    _passed("All RA conversions correct")


def test_math_dec_conversion():
    _header("Dec degree -> DMS conversion")
    _step("Dec 0 deg -> 0d 0' 0\"")
    d, m, s = sv.dec_deg_to_dms(0.0)
    _check(d == 0 and m == 0, f"Expected 0d 0', got {d}d {m}'")

    _step("Dec -33.5 deg")
    d, m, s = sv.dec_deg_to_dms(-33.5)
    _check(d == -33 and m == 30, f"Expected -33d 30', got {d}d {m}'")

    _step("Dec +45.25 deg -> 45d 15'")
    d, m, s = sv.dec_deg_to_dms(45.25)
    _check(d == 45 and m == 15, f"Expected 45d 15', got {d}d {m}'")

    _passed("All Dec conversions correct")


def test_math_eci_to_radec():
    _header("ECI->RA/Dec conversion (unit cases)")
    import numpy as np

    _step("Satellite directly north of ground station (RA should be ~90 or 270 deg, Dec > 0)")
    # Simple sanity: sat is at same x,y but higher z than ground
    sat_xyz = (0.0, 0.0, 1e7)    # directly 'above north'
    stn_xyz = (0.0, 0.0, 0.0)
    ra, dec = sv._eci_to_radec(sat_xyz, stn_xyz)
    _check(80 < dec <= 90, f"Expected Dec near 90, got {dec:.2f}")

    _step("Satellite directly to +X of station (RA should be ~0 deg)")
    sat_xyz = (1e7, 0.0, 0.0)
    ra, dec = sv._eci_to_radec(sat_xyz, stn_xyz)
    _check(abs(ra) < 1.0 or abs(ra - 360) < 1.0, f"Expected RA~0 deg, got {ra:.2f}")

    _step("RA is always in [0, 360)")
    for angle_deg in [10, 90, 179, 181, 270, 359]:
        angle_rad = np.radians(angle_deg)
        sat_xyz = (np.cos(angle_rad) * 1e7, np.sin(angle_rad) * 1e7, 0.0)
        ra, dec = sv._eci_to_radec(sat_xyz, stn_xyz)
        _check(0 <= ra < 360, f"RA={ra:.2f} out of [0, 360) for angle {angle_deg}")

    _passed("ECI->RA/Dec math validated")


def test_math_sunset_window():
    _header("Sunset window check (no ephemeris needed)")
    from datetime import timezone
    import pytz

    _step("Non-sunset daytime (noon local) should return False")
    tz = pytz.timezone("Australia/Sydney")
    noon_local = tz.localize(datetime(2026, 4, 30, 12, 0, 0))
    result = sv._during_sunset(noon_local, sv.UNSW_LOCATION, sv.UNSW_LAT, sv.UNSW_LON)
    _check(not result, "Noon should NOT be in sunset window")

    _step("Midnight local should return False")
    midnight = tz.localize(datetime(2026, 4, 30, 0, 0, 0))
    result = sv._during_sunset(midnight, sv.UNSW_LOCATION, sv.UNSW_LAT, sv.UNSW_LON)
    _check(not result, "Midnight should NOT be in sunset window")

    _passed("Sunset window logic correct")


# ── Data / ephemeris tests ───────────────────────────────────────────────────

def test_ephemeris_check():
    _header("Ephemeris file status check")
    _step(f"Checking for ephemeris at {sv.ECI_FILE}")
    status = sv.check_ephemeris()
    print(f"     exists     : {status['exists']}")
    if status["exists"]:
        print(f"     date_range : {status['date_range']}")
        print(f"     time_range : {status['time_range']}")
        _passed("Ephemeris file found and readable")
    else:
        print(f"     message    : {status['message']}")
        raise TestFailure(
            "Ephemeris file not found. Download it by running:\n"
            "  cd <autonomous-satellite-tracker dir>\n"
            "  python pyscripts/download_starlink_eph.py"
        )


def test_ephemeris_extract():
    _header("Extract ECI data for today's date")
    today = datetime.now().strftime("%Y-%m-%d")
    _step(f"Extracting records for {today}")

    status = sv.check_ephemeris()
    if not status["exists"]:
        raise TestFailure("Ephemeris file missing - run test_ephemeris_check first")

    ok = sv.extract_eci_for_date(today)
    if ok:
        _check(sv.EPH_ECI_FILE.exists(), "EPH_ECI_FILE should exist after extraction")
        _passed(f"Extracted records to {sv.EPH_ECI_FILE}")
    else:
        print(f"  NOTE: No records for {today} in ephemeris. "
              f"Available: {status['date_range']}")
        print("  This is expected if the ephemeris data doesn't include today.")
        _passed("Extract returned False cleanly (no records for today - check date range)")


def test_visibility_compute():
    _header("Full visibility computation pipeline")

    status = sv.check_ephemeris()
    if not status["exists"]:
        raise TestFailure("Ephemeris file missing")

    # Use the first available date from the ephemeris
    date_range = status["date_range"]
    first_date = date_range.split(" to ")[0].strip()
    _step(f"Computing visible satellites for {first_date} (first available date)")

    satellites = sv.compute_visible_satellites(first_date)
    print(f"  Found {len(satellites)} visible satellite records (sunset window)")

    if satellites:
        _check(sv.VISIBILITY_FILE.exists(), "visibility_result.csv should be written")
        s = satellites[0]
        _check("satellite_id" in s, "Result should have satellite_id")
        _check("ra_deg" in s and "dec_deg" in s, "Result should have ra_deg and dec_deg")
        _check(0 <= s["ra_deg"] < 360, f"RA={s['ra_deg']:.2f} out of [0, 360)")
        _check(-90 <= s["dec_deg"] <= 90, f"Dec={s['dec_deg']:.2f} out of [-90, 90]")

        print(f"\n  Sample results (first 5):")
        for sat in satellites[:5]:
            ra_h, ra_m, ra_s = sv.ra_deg_to_hours(sat["ra_deg"])
            dec_d, dec_m, dec_s = sv.dec_deg_to_dms(sat["dec_deg"])
            print(f"    [{sat['satellite_id']}] {sat['time']}"
                  f"  RA={ra_h:02d}h{ra_m:02d}m{ra_s:05.2f}s"
                  f"  Dec={dec_d:+d}\u00b0{dec_m:02d}'{dec_s:04.1f}\"")
        _passed(f"Visibility pipeline successful ({len(satellites)} records)")
    else:
        print(f"  No satellites found in sunset window for {first_date}")
        print("  This may be correct (no Starlink passes during sunset on that date).")
        _passed("Pipeline ran cleanly (0 visible satellites)")


def test_load_visibility_results():
    _header("Load visibility results from CSV")
    _step(f"Reading {sv.VISIBILITY_FILE}")
    results = sv.load_visibility_results()
    print(f"  Loaded {len(results)} records")
    if results:
        _check("satellite_id" in results[0], "Record should have satellite_id")
        _check("ra_deg" in results[0], "Record should have ra_deg")
        _passed("Visibility CSV loaded correctly")
    else:
        print("  No results file yet - run test_visibility_compute first")
        _passed("load_visibility_results returned [] cleanly (no file yet)")


# ── Hardware tests ───────────────────────────────────────────────────────────

def test_hardware_slew_to_satellite():
    _header("Slew telescope to first visible satellite [HARDWARE]")

    satellites = sv.load_visibility_results()
    if not satellites:
        raise TestFailure(
            "No visibility results. Run satellite_get_visible or test_visibility_compute first."
        )

    sat = satellites[0]
    ra_hours = sv.ra_deg_to_hours_decimal(sat["ra_deg"])
    dec_deg  = sat["dec_deg"]

    _step(f"Target satellite {sat['satellite_id']} at {sat['time']}")
    _step(f"RA={ra_hours:.4f}h  Dec={dec_deg:.4f} deg")
    print()

    from ascom_requests import telescope
    _step("Connecting telescope")
    telescope.set_connected(True)
    _check(telescope.get_connected(), "Telescope should be connected")

    _step("Unparking telescope")
    telescope.unpark()
    import time
    time.sleep(2)

    _step("Enabling tracking")
    telescope.set_tracking(True)

    _step(f"Slewing to satellite RA={ra_hours:.4f}h  Dec={dec_deg:.4f} deg")
    telescope.slew_to_coordinates_async(ra_hours, dec_deg)

    deadline = time.time() + 120
    while time.time() < deadline:
        if not telescope.get_slewing():
            break
        time.sleep(2)
    else:
        raise TestFailure("Telescope still slewing after 120s")

    actual_ra  = telescope.get_rightascension()
    actual_dec = telescope.get_declination()
    _step(f"Arrived at RA={actual_ra:.4f}h  Dec={actual_dec:.4f} deg")

    ra_err_h   = abs(actual_ra - ra_hours)
    dec_err_deg = abs(actual_dec - dec_deg)
    _check(ra_err_h < 0.05, f"RA error {ra_err_h:.4f}h > 0.05h tolerance")
    _check(dec_err_deg < 1.0, f"Dec error {dec_err_deg:.2f}° > 1.0° tolerance")

    _passed(f"Telescope on target (RA err={ra_err_h:.4f}h, Dec err={dec_err_deg:.2f}°)")


# ── Test registry & runner ───────────────────────────────────────────────────

TESTS = [
    # (name, function, requires_hardware)
    ("math_ra_conversion",       test_math_ra_conversion,       False),
    ("math_dec_conversion",      test_math_dec_conversion,       False),
    ("math_eci_to_radec",        test_math_eci_to_radec,         False),
    ("math_sunset_window",       test_math_sunset_window,        False),
    ("ephemeris_check",          test_ephemeris_check,           False),
    ("ephemeris_extract",        test_ephemeris_extract,         False),
    ("visibility_compute",       test_visibility_compute,        False),
    ("load_visibility_results",  test_load_visibility_results,   False),
    ("hardware_slew_to_satellite", test_hardware_slew_to_satellite, True),
]


def run_tests(selected: list[str] | None = None, skip_hardware: bool = False):
    passed = failed = skipped = 0

    for name, func, requires_hw in TESTS:
        if selected and name not in selected:
            continue
        if requires_hw and skip_hardware:
            print(f"\n[SKIP] {name} (hardware test, use without --skip-hardware to run)")
            skipped += 1
            continue

        try:
            func()
            passed += 1
        except TestFailure as e:
            print(f"\n  *** {e}")
            failed += 1
        except Exception as e:
            print(f"\n  *** UNEXPECTED ERROR in {name}: {type(e).__name__}: {e}")
            failed += 1

    print(f"\n{'='*60}")
    print(f"  Results: {passed} passed, {failed} failed, {skipped} skipped")
    print(f"{'='*60}\n")
    return failed == 0


def main():
    parser = argparse.ArgumentParser(description="Satellite visibility test suite")
    parser.add_argument("--list", action="store_true", help="List available tests")
    parser.add_argument("--test", help="Comma-separated test names to run")
    parser.add_argument("--skip-hardware", action="store_true",
                        help="Skip tests that require telescope hardware")
    args = parser.parse_args()

    if args.list:
        print("Available tests:")
        for name, _, hw in TESTS:
            tag = " [hardware]" if hw else ""
            print(f"  {name}{tag}")
        return

    selected = [t.strip() for t in args.test.split(",")] if args.test else None
    ok = run_tests(selected, skip_hardware=args.skip_hardware)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
