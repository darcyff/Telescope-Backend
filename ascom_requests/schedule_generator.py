"""
Schedule Generator
==================
Generates a JSON schedule file for the scheduler from satellite pass data
produced by the Autonomous Satellite Tracker.

The satellite tracker outputs visibility data with RA/Dec positions at specific
times. This module converts that into a full observatory schedule: connecting
devices, opening the dome, slewing to each position, capturing images, and
tearing down afterwards.

Usage:
    # From satellite pass data (list of dicts)
    from ascom_requests.schedule_generator import generate_schedule

    passes = [
        {"time": "2026-06-22 19:09:42", "ra_deg": 93.85, "dec_deg": -3.14},
        {"time": "2026-06-22 19:21:42", "ra_deg": 96.88, "dec_deg": -3.03},
    ]
    schedule = generate_schedule(
        satellite_name="STARLINK-1234",
        passes=passes,
        exposure_duration=5.0,
    )

    # From a visibility_result.csv file
    from ascom_requests.schedule_generator import generate_schedule_from_csv
    schedule = generate_schedule_from_csv("path/to/visibility_result.csv")
"""

import json
import os
import argparse
from datetime import datetime, timedelta


SETUP_LEAD_TIME_SECONDS = 300
SLEW_SETTLE_SECONDS = 120
DEFAULT_EXPOSURE = 5.0
DEFAULT_GAIN = 100
DEFAULT_OFFSET = 10
DEFAULT_COOLER_TEMP = -10.0
SCHEDULES_DIR = os.path.join(os.path.dirname(__file__), "schedules")


def ra_deg_to_hours(ra_deg: float) -> float:
    """Convert RA from degrees (0-360) to hours (0-24)."""
    return (ra_deg % 360) / 15.0


def generate_schedule(
    satellite_name: str,
    passes: list[dict],
    exposure_duration: float = DEFAULT_EXPOSURE,
    gain: int = DEFAULT_GAIN,
    offset: int = DEFAULT_OFFSET,
    cooler_temp: float | None = DEFAULT_COOLER_TEMP,
    setup_lead_time: int = SETUP_LEAD_TIME_SECONDS,
) -> dict:
    """
    Generate a complete observatory schedule from satellite pass data.

    Args:
        satellite_name: Name/ID of the satellite being observed.
        passes: List of dicts with keys:
            - time: ISO-8601 or "YYYY-MM-DD HH:MM:SS" string
            - ra_deg: Right ascension in degrees (0-360)
            - dec_deg: Declination in degrees
        exposure_duration: Camera exposure time in seconds.
        gain: Camera gain setting.
        offset: Camera offset setting.
        cooler_temp: CCD cooler target temperature (None to skip cooling).
        setup_lead_time: Seconds before first pass to start setup.

    Returns:
        Dict in the scheduler's JSON format with a "schedule" key.
    """
    if not passes:
        raise ValueError("No pass data provided")

    parsed = []
    for p in passes:
        t = p["time"]
        if isinstance(t, str):
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    t = datetime.strptime(t, fmt)
                    break
                except ValueError:
                    continue
            else:
                t = datetime.fromisoformat(t)
        parsed.append({
            "time": t,
            "ra_hours": ra_deg_to_hours(p["ra_deg"]),
            "dec": p["dec_deg"],
        })

    parsed.sort(key=lambda x: x["time"])

    first_time = parsed[0]["time"]
    setup_time = first_time - timedelta(seconds=setup_lead_time)

    schedule = []

    def add(action, params=None, time=None):
        entry = {"action": action}
        if time is not None:
            entry["time"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        if params:
            entry["params"] = params
        schedule.append(entry)

    # -- Setup phase (timed start, then sequential) --
    add("dome_connect", time=setup_time)
    add("telescope_connect")
    add("camera_connect")
    add("telescope_unpark")
    add("dome_findhome")
    add("shutter_open")
    add("dome_slave", {"enabled": True})
    add("telescope_tracking", {"enabled": True})
    if cooler_temp is not None:
        add("camera_cooler", {"enabled": True, "temp": cooler_temp})
    add("camera_set_gain", {"gain": gain})
    add("camera_set_offset", {"offset": offset})

    # -- Observation phase: slew and capture at each pass point --
    for i, obs in enumerate(parsed):
        slew_time = obs["time"] - timedelta(seconds=SLEW_SETTLE_SECONDS)

        add("telescope_slew_radec",
            {"ra": round(obs["ra_hours"], 6), "dec": round(obs["dec"], 6)},
            time=slew_time)

        save_path = os.path.join(
            "captures",
            _sanitize(satellite_name),
            f"obs_{i:03d}_{obs['time'].strftime('%H%M%S')}.fits",
        )
        add("camera_capture",
            {"duration": exposure_duration, "save_path": save_path},
            time=obs["time"])

    # -- Teardown phase (timed start after last capture, then sequential) --
    teardown_time = parsed[-1]["time"] + timedelta(seconds=int(exposure_duration) + 10)

    add("shutter_close", time=teardown_time)
    add("dome_slave", {"enabled": False})
    add("telescope_tracking", {"enabled": False})
    add("telescope_park")
    add("dome_findhome")
    if cooler_temp is not None:
        add("camera_cooler", {"enabled": False})
    add("camera_disconnect")
    add("telescope_disconnect")
    add("dome_disconnect")

    warnings = _check_time_ordering(schedule)

    result = {
        "metadata": {
            "satellite": satellite_name,
            "generated": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "observations": len(parsed),
            "first_pass": parsed[0]["time"].strftime("%Y-%m-%dT%H:%M:%S"),
            "last_pass": parsed[-1]["time"].strftime("%Y-%m-%dT%H:%M:%S"),
        },
        "schedule": schedule,
    }
    if warnings:
        result["warnings"] = warnings
    return result


def generate_schedule_from_csv(
    csv_path: str,
    satellite_name: str | None = None,
    **kwargs,
) -> dict:
    """
    Generate a schedule from a visibility_result.csv produced by the
    Autonomous Satellite Tracker.

    CSV format: Time (s), RA (deg), Dec (deg)

    Args:
        csv_path: Path to the visibility CSV file.
        satellite_name: Override satellite name (defaults to filename).
        **kwargs: Forwarded to generate_schedule().
    """
    import csv

    if satellite_name is None:
        satellite_name = os.path.splitext(os.path.basename(csv_path))[0]

    passes = []
    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            passes.append({
                "time": row["Time (s)"].strip(),
                "ra_deg": float(row["RA (deg)"]),
                "dec_deg": float(row["Dec (deg)"]),
            })

    return generate_schedule(satellite_name=satellite_name, passes=passes, **kwargs)


def save_schedule(schedule: dict, output_path: str | None = None) -> str:
    """Save a schedule dict to a JSON file and return the path."""
    if output_path is None:
        sat = _sanitize(schedule["metadata"]["satellite"])
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        os.makedirs(SCHEDULES_DIR, exist_ok=True)
        output_path = os.path.join(SCHEDULES_DIR, f"satellite_{sat}_{stamp}.json")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(schedule, f, indent=2)
    return output_path


def _check_time_ordering(schedule: list[dict]) -> list[str]:
    """Check for timed entries that are scheduled before an earlier timed entry."""
    warnings = []
    last_time = None
    last_index = None
    for i, entry in enumerate(schedule):
        t = entry.get("time")
        if t is None:
            continue
        if last_time is not None and t < last_time:
            warnings.append(
                f"Entry {i} ({entry['action']}) at {t} is scheduled before "
                f"entry {last_index} ({schedule[last_index]['action']}) at {last_time} "
                f"— it will run late instead of at its scheduled time"
            )
        last_time = t
        last_index = i
    return warnings


def _sanitize(name: str) -> str:
    """Sanitize a name for use in file paths."""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


# ── CLI ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate an observatory schedule from satellite pass data")
    parser.add_argument("csv", help="Path to visibility_result.csv from the satellite tracker")
    parser.add_argument("--name", help="Satellite name (default: derived from filename)")
    parser.add_argument("--exposure", type=float, default=DEFAULT_EXPOSURE,
                        help=f"Exposure duration in seconds (default: {DEFAULT_EXPOSURE})")
    parser.add_argument("--gain", type=int, default=DEFAULT_GAIN,
                        help=f"Camera gain (default: {DEFAULT_GAIN})")
    parser.add_argument("--offset", type=int, default=DEFAULT_OFFSET,
                        help=f"Camera offset (default: {DEFAULT_OFFSET})")
    parser.add_argument("--no-cooler", action="store_true",
                        help="Skip CCD cooler setup")
    parser.add_argument("--cooler-temp", type=float, default=DEFAULT_COOLER_TEMP,
                        help=f"CCD cooler target temp (default: {DEFAULT_COOLER_TEMP})")
    parser.add_argument("--lead-time", type=int, default=SETUP_LEAD_TIME_SECONDS,
                        help=f"Setup lead time in seconds before first pass (default: {SETUP_LEAD_TIME_SECONDS})")
    parser.add_argument("--output", "-o", help="Output JSON path (default: auto-generated)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the schedule to stdout without saving")
    args = parser.parse_args()

    schedule = generate_schedule_from_csv(
        csv_path=args.csv,
        satellite_name=args.name,
        exposure_duration=args.exposure,
        gain=args.gain,
        offset=args.offset,
        cooler_temp=None if args.no_cooler else args.cooler_temp,
        setup_lead_time=args.lead_time,
    )

    if args.dry_run:
        print(json.dumps(schedule, indent=2))
        if schedule.get("warnings"):
            print(f"\nWARNINGS ({len(schedule['warnings'])}):", file=__import__('sys').stderr)
            for w in schedule["warnings"]:
                print(f"  ⚠ {w}", file=__import__('sys').stderr)
        return

    path = save_schedule(schedule, args.output)
    print(f"Schedule saved to: {path}")
    print(f"  Satellite:    {schedule['metadata']['satellite']}")
    print(f"  Observations: {schedule['metadata']['observations']}")
    print(f"  First pass:   {schedule['metadata']['first_pass']}")
    print(f"  Last pass:    {schedule['metadata']['last_pass']}")
    if schedule.get("warnings"):
        print(f"\nWARNINGS ({len(schedule['warnings'])}):")
        for w in schedule["warnings"]:
            print(f"  ⚠ {w}")
    print(f"\nRun with:  python -m ascom_requests.scheduler {path}")


if __name__ == "__main__":
    main()
