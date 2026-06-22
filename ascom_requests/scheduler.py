"""
Observation Scheduler
=====================
Reads a JSON schedule file and executes telescope/dome actions at specified
times using the ASCOM Alpaca wrappers in telescope.py and dome.py.

Usage:
    python scheduler.py schedule.json
    python scheduler.py schedule.json --dry-run
    python scheduler.py schedule.json --log

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
from datetime import datetime, timezone, tzinfo as _tzinfo

# Allow importing as a package when run as a script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ascom_requests import telescope
from ascom_requests import dome
from ascom_requests import camera
from ascom_requests import shutter

# ── Constants ───────────────────────────────────────────────────────────

POLL_INTERVAL = 2        # seconds between slew-status polls
SLEW_TIMEOUT = 120       # seconds before a slew is considered stuck
HOME_TIMEOUT = 180       # dome home search can be slow
SHUTTER_TIMEOUT = 60     # seconds for shutter open/close
CAPTURE_DIR = "captures" # default directory for saved FITS files

log = logging.getLogger("scheduler")


def _local_tz() -> _tzinfo:
    """Return the system's local timezone."""
    return datetime.now().astimezone().tzinfo


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


def dome_findhome(**kw):
    log.info("Dome finding home")
    dome.findhome()
    _wait_dome_slew(timeout=HOME_TIMEOUT)
    log.info("Dome at home")


def dome_slave(enabled: bool, **kw):
    dome.set_slaved(enabled)
    log.info(f"Dome slaving {'enabled' if enabled else 'disabled'}")


def dome_abort(**kw):
    dome.abort_slew()
    log.info("Dome slew aborted")


# ── Shutter actions ───────────────────────────────────────────────────

def _wait_shutter(target_status: int, timeout=SHUTTER_TIMEOUT):
    """Poll until the shutter reaches the target status (0=Open, 1=Closed)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = shutter.get_status()
        if s == target_status:
            return
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"Shutter did not reach status {target_status} after {timeout}s")


def shutter_open(**kw):
    log.info("Opening shutter")
    shutter.open_shutter()
    _wait_shutter(0)
    log.info("Shutter open")


def shutter_close(**kw):
    log.info("Closing shutter")
    shutter.close_shutter()
    _wait_shutter(1)
    log.info("Shutter closed")


# ── Camera actions ────────────────────────────────────────────────────

def camera_connect(**kw):
    camera.set_connected(True)
    log.info("Camera connected")


def camera_disconnect(**kw):
    camera.set_connected(False)
    log.info("Camera disconnected")


def camera_cooler(enabled: bool, temp: float | None = None, **kw):
    camera.set_cooler_on(enabled)
    if enabled and temp is not None:
        camera.set_ccd_temperature(temp)
        log.info(f"Camera cooler on, target {temp}°C")
    else:
        log.info(f"Camera cooler {'on' if enabled else 'off'}")


def camera_set_gain(gain: int, **kw):
    camera.set_gain(gain)
    log.info(f"Camera gain set to {gain}")


def camera_set_offset(offset: int, **kw):
    camera.set_offset(offset)
    log.info(f"Camera offset set to {offset}")


def camera_capture(duration: float, save_path: str | None = None,
                   light: bool = True, **kw):
    if save_path is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        save_path = os.path.join(CAPTURE_DIR, f"capture_{stamp}.fits")
    log.info(f"Capturing {duration}s exposure -> {save_path}")
    camera.capture(duration, save_path, light=light)
    log.info(f"Image saved: {save_path}")


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
    "dome_findhome":          dome_findhome,
    "dome_slave":             dome_slave,
    "dome_abort":             dome_abort,
    "shutter_open":           shutter_open,
    "shutter_close":          shutter_close,
    "camera_connect":         camera_connect,
    "camera_disconnect":      camera_disconnect,
    "camera_cooler":          camera_cooler,
    "camera_set_gain":        camera_set_gain,
    "camera_set_offset":      camera_set_offset,
    "camera_capture":         camera_capture,
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
                t = t.replace(tzinfo=_local_tz())
            t = t.astimezone(timezone.utc)
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
    local = _local_tz()
    for e in entries:
        t = e["time"].astimezone(local).isoformat() if e["time"] else "(after previous)"
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
            local_time = e['time'].astimezone(_local_tz()).isoformat()
            if wait_seconds > 0:
                log.info(f"Waiting {wait_seconds:.0f}s until {local_time} "
                         f"for entry {e['index']} ({e['action']})")
                time.sleep(wait_seconds)
            elif wait_seconds < -60:
                log.warning(f"Entry {e['index']} was scheduled for "
                            f"{local_time} ({-wait_seconds:.0f}s ago) "
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
        {"time": "2026-04-16T20:00:15", "action": "dome_slave", "params": {"enabled": True}},
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
        {"time": "2026-04-17T04:00:20", "action": "dome_findhome"},
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
    parser.add_argument("--log", action="store_true",
                        help="Save log files to logs/schedules")
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

    setup_logging("logs/schedules" if args.log else None)

    entries = load_schedule(args.schedule)
    run_schedule(entries, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
