"""
Observation Scheduler
=====================
Reads a JSON schedule file and executes telescope/dome actions at specified
times using the ASCOM Alpaca wrappers in telescope.py and dome.py.

Usage:
    python scheduler.py schedule.json
    python scheduler.py schedule.json --dry-run
    python scheduler.py schedule.json --log-dir logs/schedules

Schedule format (see example at bottom of file or generate with --example):
    {
      "schedule": [
        {
          "time": "2026-04-16T21:00:00",
          "action": "telescope_slew_radec",
          "params": {"ra": 5.278, "dec": -8.2}
        },
        ...
      ]
    }
"""

import sys
import os
import json
import time
import argparse
import logging
from datetime import datetime, timezone

# Allow importing as a package when run as a script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ascom_requests import telescope
from ascom_requests import dome
from ascom_requests import satellite_visibility as sv

# ── Constants ───────────────────────────────────────────────────────────

POLL_INTERVAL = 2        # seconds between slew-status polls
SLEW_TIMEOUT = 120       # seconds before a slew is considered stuck
HOME_TIMEOUT = 180       # dome home search can be slow

log = logging.getLogger("scheduler")


# ── Action helpers ──────────────────────────────────────────────────────

def _wait_telescope_slew(timeout=SLEW_TIMEOUT):
    """Poll until the telescope reports it is no longer slewing."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not telescope.get_slewing():
            return
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"Telescope still slewing after {timeout}s")


def _wait_dome_slew(timeout=SLEW_TIMEOUT):
    """Poll until the dome reports it is no longer slewing."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not dome.get_slewing():
            return
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"Dome still slewing after {timeout}s")


# Each handler receives **params from the schedule entry.

def telescope_connect(**kw):
    telescope.set_connected(True)
    log.info("Telescope connected")


def telescope_disconnect(**kw):
    telescope.set_connected(False)
    log.info("Telescope disconnected")


def telescope_slew_radec(ra: float, dec: float, **kw):
    log.info(f"Slewing telescope to RA={ra:.4f}h  Dec={dec:.4f}\u00b0")
    telescope.slew_to_coordinates_async(ra, dec)
    _wait_telescope_slew()
    actual_ra = telescope.get_rightascension()
    actual_dec = telescope.get_declination()
    log.info(f"Telescope arrived at RA={actual_ra:.4f}h  Dec={actual_dec:.4f}\u00b0")


def telescope_slew_altaz(az: float, alt: float, **kw):
    log.info(f"Slewing telescope to Az={az:.2f}\u00b0  Alt={alt:.2f}\u00b0")
    telescope.slew_to_altaz_async(az, alt)
    _wait_telescope_slew()
    log.info(f"Telescope arrived at Az={telescope.get_azimuth():.2f}\u00b0  "
             f"Alt={telescope.get_altitude():.2f}\u00b0")


def telescope_park(**kw):
    log.info("Parking telescope")
    telescope.park()
    _wait_telescope_slew()
    log.info("Telescope parked")


def telescope_unpark(**kw):
    telescope.unpark()
    log.info("Telescope unparked")


def telescope_findhome(**kw):
    log.info("Telescope finding home")
    telescope.findhome()
    _wait_telescope_slew(timeout=HOME_TIMEOUT)
    log.info("Telescope at home")


def telescope_tracking(enabled: bool, **kw):
    telescope.set_tracking(enabled)
    log.info(f"Telescope tracking {'enabled' if enabled else 'disabled'}")


def telescope_abort(**kw):
    telescope.abort_slew()
    log.info("Telescope slew aborted")


def dome_connect(**kw):
    dome.set_connected(True)
    log.info("Dome connected")


def dome_disconnect(**kw):
    dome.set_connected(False)
    log.info("Dome disconnected")


def dome_slew_az(az: float, **kw):
    log.info(f"Slewing dome to Az={az:.2f}\u00b0")
    dome.slew_to_azimuth(az)
    _wait_dome_slew()
    log.info(f"Dome arrived at Az={dome.get_azimuth():.2f}\u00b0")


def dome_slew_alt(alt: float, **kw):
    log.info(f"Slewing dome to Alt={alt:.2f}\u00b0")
    dome.slew_to_altitude(alt)
    _wait_dome_slew()
    log.info(f"Dome arrived at Alt={dome.get_altitude():.2f}\u00b0")


def dome_park(**kw):
    log.info("Parking dome")
    dome.park()
    _wait_dome_slew()
    log.info("Dome parked")


def dome_findhome(**kw):
    log.info("Dome finding home")
    dome.findhome()
    _wait_dome_slew(timeout=HOME_TIMEOUT)
    log.info("Dome at home")


def dome_open_shutter(**kw):
    dome.open_shutter()
    log.info("Dome shutter opened")


def dome_close_shutter(**kw):
    dome.close_shutter()
    log.info("Dome shutter closed")


def dome_slave(enabled: bool, **kw):
    dome.set_slaved(enabled)
    log.info(f"Dome slaving {'enabled' if enabled else 'disabled'}")


def dome_abort(**kw):
    dome.abort_slew()
    log.info("Dome slew aborted")


# ── Satellite visibility actions ─────────────────────────────────────────

def satellite_get_visible(
    date: str = None,
    location: str = sv.UNSW_LOCATION,
    lat: float = sv.UNSW_LAT,
    lon: float = sv.UNSW_LON,
    elevation: float = sv.UNSW_ELEVATION,
    sunset_window_minutes: int = 30,
    **kw,
):
    """
    Compute visible Starlink satellites and log their RA/Dec.

    Reads orbital states from the autonomous-satellite-tracker ephemeris,
    filters by sunset window, converts ECI->RA/Dec, and writes results to
    the tracker's visibility_result.csv.

    Schedule params:
        date (str, optional): "YYYY-MM-DD" - defaults to today.
        location (str): pytz timezone in "Country/City" form. Default: Australia/Sydney.
        lat (float): Observatory latitude in degrees. Default: UNSW.
        lon (float): Observatory longitude in degrees. Default: UNSW.
        elevation (float): Observatory elevation in metres. Default: UNSW.
        sunset_window_minutes (int): Minutes either side of sunset. Default: 30.
    """
    if date is None:
        from datetime import datetime as _dt
        date = _dt.now().strftime("%Y-%m-%d")

    log.info(f"Computing satellite visibility for {date} at {location} "
             f"({lat:.4f}\u00b0, {lon:.4f}\u00b0, {elevation:.0f}m)")

    status = sv.check_ephemeris()
    if not status["exists"]:
        raise RuntimeError(status["message"])
    log.info(f"Ephemeris: {status['date_range']}  |  {status['time_range']}")

    satellites = sv.compute_visible_satellites(
        date, location, lat, lon, elevation, sunset_window_minutes
    )

    if not satellites:
        log.warning(f"No visible satellites found for {date} within ±{sunset_window_minutes}min of sunset")
        return

    log.info(f"Found {len(satellites)} visible satellite records:")
    for s in satellites:
        ra_h, ra_m, ra_s = sv.ra_deg_to_hours(s["ra_deg"])
        dec_d, dec_m, dec_s = sv.dec_deg_to_dms(s["dec_deg"])
        log.info(
            f"  [{s['satellite_id']}] {s['time']}  "
            f"RA={ra_h:02d}h{ra_m:02d}m{ra_s:05.2f}s  "
            f"Dec={dec_d:+03d}\u00b0{dec_m:02d}'{dec_s:04.1f}\""
        )
    log.info(f"Results saved to {sv.VISIBILITY_FILE}")


def satellite_slew_to_next(index: int = 0, **kw):
    """
    Slew the telescope to a satellite from the most recent visibility results.

    Reads visibility_result.csv produced by satellite_get_visible and slews
    to the satellite at the given index (0 = first visible).

    Schedule params:
        index (int): Index into the visibility results list. Default: 0.
    """
    satellites = sv.load_visibility_results()
    if not satellites:
        raise RuntimeError(
            "No visibility results found. Run satellite_get_visible first."
        )
    if index >= len(satellites):
        raise IndexError(
            f"index={index} but only {len(satellites)} satellite records available"
        )

    sat = satellites[index]
    ra_hours = sv.ra_deg_to_hours_decimal(sat["ra_deg"])
    dec_deg  = sat["dec_deg"]

    log.info(
        f"Slewing to satellite {sat['satellite_id']} "
        f"(index {index}/{len(satellites)-1})  "
        f"RA={ra_hours:.4f}h  Dec={dec_deg:.4f}\u00b0  "
        f"(from visibility time {sat['time']})"
    )
    telescope.slew_to_coordinates_async(ra_hours, dec_deg)
    _wait_telescope_slew()
    actual_ra  = telescope.get_rightascension()
    actual_dec = telescope.get_declination()
    log.info(f"Telescope arrived at RA={actual_ra:.4f}h  Dec={actual_dec:.4f}\u00b0")


# ── Action registry ─────────────────────────────────────────────────────

ACTIONS = {
    "telescope_connect":      telescope_connect,
    "telescope_disconnect":   telescope_disconnect,
    "telescope_slew_radec":   telescope_slew_radec,
    "telescope_slew_altaz":   telescope_slew_altaz,
    "telescope_park":         telescope_park,
    "telescope_unpark":       telescope_unpark,
    "telescope_findhome":     telescope_findhome,
    "telescope_tracking":     telescope_tracking,
    "telescope_abort":        telescope_abort,
    "dome_connect":           dome_connect,
    "dome_disconnect":        dome_disconnect,
    "dome_slew_az":           dome_slew_az,
    "dome_slew_alt":          dome_slew_alt,
    "dome_park":              dome_park,
    "dome_findhome":          dome_findhome,
    "dome_open_shutter":      dome_open_shutter,
    "dome_close_shutter":     dome_close_shutter,
    "dome_slave":             dome_slave,
    "dome_abort":             dome_abort,
    # Satellite visibility
    "satellite_get_visible":  satellite_get_visible,
    "satellite_slew_to_next": satellite_slew_to_next,
}


# ── Schedule loading & validation ───────────────────────────────────────

def load_schedule(path: str) -> list[dict]:
    with open(path) as f:
        data = json.load(f)

    entries = data.get("schedule", [])
    if not entries:
        raise ValueError("Schedule is empty or missing 'schedule' key")

    parsed = []
    for i, entry in enumerate(entries):
        if "action" not in entry:
            raise ValueError(f"Entry {i} missing 'action'")
        if entry["action"] not in ACTIONS:
            raise ValueError(f"Entry {i}: unknown action '{entry['action']}'. "
                             f"Available: {', '.join(sorted(ACTIONS))}")

        time_str = entry.get("time")
        if time_str:
            try:
                t = datetime.fromisoformat(time_str)
            except ValueError:
                raise ValueError(f"Entry {i}: invalid time '{time_str}' "
                                 "(use ISO-8601, e.g. 2026-04-16T21:00:00)")
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
        else:
            t = None  # no time means "run immediately after the previous entry"

        parsed.append({
            "index": i,
            "time": t,
            "action": entry["action"],
            "params": entry.get("params", {}),
        })

    # Sort by time; entries without a time keep their original order
    # relative to the previous timed entry.
    return parsed


def print_schedule(entries: list[dict]):
    log.info(f"{'#':<4} {'Time':<26} {'Action':<25} Params")
    log.info("-" * 80)
    for e in entries:
        t = e["time"].isoformat() if e["time"] else "(after previous)"
        p = json.dumps(e["params"]) if e["params"] else ""
        log.info(f"{e['index']:<4} {t:<26} {e['action']:<25} {p}")


# ── Execution ───────────────────────────────────────────────────────────

def run_schedule(entries: list[dict], dry_run: bool = False):
    log.info(f"Schedule loaded: {len(entries)} entries")
    print_schedule(entries)

    if dry_run:
        log.info("Dry-run mode: no actions will be executed")
        return

    for e in entries:
        # Wait until the scheduled time
        if e["time"] is not None:
            now = datetime.now(timezone.utc)
            wait_seconds = (e["time"] - now).total_seconds()
            if wait_seconds > 0:
                log.info(f"Waiting {wait_seconds:.0f}s until {e['time'].isoformat()} "
                         f"for entry {e['index']} ({e['action']})")
                time.sleep(wait_seconds)
            elif wait_seconds < -60:
                log.warning(f"Entry {e['index']} was scheduled for "
                            f"{e['time'].isoformat()} ({-wait_seconds:.0f}s ago) "
                            "- running now")

        handler = ACTIONS[e["action"]]
        log.info(f"[{e['index']}] Executing: {e['action']}")
        try:
            handler(**e["params"])
            log.info(f"[{e['index']}] {e['action']} completed")
        except Exception as exc:
            log.error(f"[{e['index']}] {e['action']} FAILED: {exc}")
            raise


# ── Example schedule generation ─────────────────────────────────────────

EXAMPLE_SCHEDULE = {
    "schedule": [
        {"time": "2026-04-16T20:00:00", "action": "telescope_connect"},
        {"time": "2026-04-16T20:00:05", "action": "dome_connect"},
        {"time": "2026-04-16T20:00:10", "action": "telescope_unpark"},
        {"time": "2026-04-16T20:00:15", "action": "dome_open_shutter"},
        {"time": "2026-04-16T20:00:20", "action": "dome_slave", "params": {"enabled": True}},
        {"time": "2026-04-16T20:00:30", "action": "telescope_tracking", "params": {"enabled": True}},
        {
            "time": "2026-04-16T21:00:00",
            "action": "telescope_slew_radec",
            "params": {"ra": 5.278, "dec": -8.2},
        },
        {
            "time": "2026-04-16T22:30:00",
            "action": "telescope_slew_radec",
            "params": {"ra": 16.695, "dec": -26.432},
        },
        {
            "time": "2026-04-17T04:00:00",
            "action": "dome_slave",
            "params": {"enabled": False},
        },
        {"time": "2026-04-17T04:00:05", "action": "telescope_tracking", "params": {"enabled": False}},
        {"time": "2026-04-17T04:00:10", "action": "telescope_park"},
        {"time": "2026-04-17T04:00:20", "action": "dome_close_shutter"},
        {"time": "2026-04-17T04:00:30", "action": "dome_park"},
        {"time": "2026-04-17T04:01:00", "action": "telescope_disconnect"},
        {"time": "2026-04-17T04:01:05", "action": "dome_disconnect"},
    ]
}


# ── CLI ─────────────────────────────────────────────────────────────────

def setup_logging(log_dir: str | None = None):
    fmt = "%(asctime)s  %(levelname)-7s  %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler()]

    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = os.path.join(log_dir, f"schedule_{stamp}.log")
        handlers.append(logging.FileHandler(log_path))
        print(f"Logging to {log_path}")

    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)


def main():
    parser = argparse.ArgumentParser(
        description="Run a telescope/dome observation schedule")
    parser.add_argument("schedule", nargs="?",
                        help="Path to a JSON schedule file")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate and print the schedule without executing")
    parser.add_argument("--log-dir", default=None,
                        help="Directory for log files")
    parser.add_argument("--example", action="store_true",
                        help="Print an example schedule JSON and exit")
    parser.add_argument("--list-actions", action="store_true",
                        help="List all available actions and exit")
    args = parser.parse_args()

    if args.example:
        print(json.dumps(EXAMPLE_SCHEDULE, indent=2))
        return

    if args.list_actions:
        for name in sorted(ACTIONS):
            print(f"  {name}")
        return

    if not args.schedule:
        parser.error("A schedule file is required (or use --example / --list-actions)")

    setup_logging(args.log_dir)

    entries = load_schedule(args.schedule)
    run_schedule(entries, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
