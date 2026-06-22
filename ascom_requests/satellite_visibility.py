"""
Satellite Visibility Module
===========================
Integrates the autonomous-satellite-tracker's ECI->RA/Dec visibility analysis
into the telescope backend.

Source algorithm:
    Autonomous-satellite-tracker/pyscripts/ephemerisPipeline/ephemerisFunctions.py
    (by Julia)

The module reads Starlink orbital state data from the satellite tracker's
ephemeris CSV files, filters for the sunset observation window, and converts
ECI coordinates to RA/Dec for telescope slewing.

Usage:
    from ascom_requests import satellite_visibility as sv

    status = sv.check_ephemeris()
    satellites = sv.compute_visible_satellites("2026-04-30")
    for sat in satellites:
        print(sat["satellite_id"], sat["ra_deg"], sat["dec_deg"])

    # Or for today's date:
    satellites = sv.get_visible_satellites_today()
"""

import csv
import os
import numpy as np
import pytz
from datetime import datetime, timedelta
from pathlib import Path
from astropy import coordinates as coord
from astropy import units as u
from astropy.time import Time
from astral import LocationInfo
from astral.sun import sun

# ── Satellite tracker data directory ────────────────────────────────────────

TRACKER_DIR = Path(r"C:\Users\darcy\Desktop\Thesis\Autonomous-satellite-tracker\autonomous-satellite-tracker-main")

# Data file paths (inside tracker directory, matching ephemerisFunctions.py)
ECI_FILE         = TRACKER_DIR / "orbital_states_eci.csv"
EPH_ECI_FILE     = TRACKER_DIR / "orbital_states_eph_eci.csv"
VISIBILITY_FILE  = TRACKER_DIR / "out" / "visibility_result.csv"


# ── UNSW Kensington Observatory defaults ────────────────────────────────────
# Format: "Country/City" split on '/' gives country+city for astral;
# the full string is also a valid pytz timezone identifier.

UNSW_LOCATION  = "Australia/Sydney"
UNSW_LAT       = -33.9173   # degrees
UNSW_LON       = 151.2313   # degrees
UNSW_ELEVATION = 40.0       # metres above sea level


# ── Ephemeris file helpers ───────────────────────────────────────────────────

def check_ephemeris() -> dict:
    """
    Report the status of the satellite tracker's ephemeris file.

    Returns:
        dict with keys:
            exists      (bool)
            date_range  (str)  e.g. "2026-04-28 to 2026-04-30"
            time_range  (str)  e.g. "18:00 to 23:59"
            path        (str)
    """
    if not ECI_FILE.exists():
        return {
            "exists": False,
            "message": f"Ephemeris file not found at {ECI_FILE}. "
                       "Run download_starlink_eph.py from the satellite tracker.",
            "path": str(ECI_FILE),
        }

    min_date = max_date = min_dt = max_dt = None
    with open(ECI_FILE, newline="") as f:
        for row in csv.DictReader(f):
            dt = datetime.strptime(row["time"], "%Y-%m-%d %H:%M:%S")
            d = dt.date()
            if min_date is None or d < min_date:
                min_date = d
            if max_date is None or d > max_date:
                max_date = d
            if min_dt is None or dt < min_dt:
                min_dt = dt
            if max_dt is None or dt > max_dt:
                max_dt = dt

    return {
        "exists": True,
        "date_range": f"{min_date} to {max_date}",
        "time_range": (f"{min_dt.strftime('%H:%M')} to {max_dt.strftime('%H:%M')}"
                       if min_dt else "unknown"),
        "path": str(ECI_FILE),
    }


def extract_eci_for_date(date_str: str) -> bool:
    """
    Filter the full ephemeris CSV to a single date and write to EPH_ECI_FILE.

    Args:
        date_str: "YYYY-MM-DD"

    Returns:
        True if records were found and written, False otherwise.
    """
    target = datetime.strptime(date_str, "%Y-%m-%d").date()
    rows = []

    with open(ECI_FILE, newline="") as f:
        for row in csv.DictReader(f):
            dt = datetime.strptime(row["time"], "%Y-%m-%d %H:%M:%S")
            if dt.date() == target:
                rows.append([dt, row["position"], row["velocity"], row["satellite_id"]])

    if not rows:
        return False

    rows.sort(key=lambda r: r[0])
    with open(EPH_ECI_FILE, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "position", "velocity", "satellite_id"])
        w.writerows(rows)
    return True


# ── Coordinate conversion (same algorithm as ephemerisFunctions.py) ─────────

def _ground_eci(lat: float, lon: float, elevation: float, obs_time) -> tuple:
    """Convert geodetic observatory coords to ECI (GCRS) at obs_time."""
    t = Time(obs_time)
    loc = coord.EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=elevation * u.m)
    gcrs = loc.get_itrs(obstime=t).transform_to(coord.GCRS(obstime=t))
    cart = gcrs.cartesian
    return (
        cart.x.to(u.m).value,
        cart.y.to(u.m).value,
        cart.z.to(u.m).value,
    )


def _eci_to_radec(sat_xyz: tuple, ground_xyz: tuple) -> tuple:
    """Convert satellite ECI position to RA/Dec as seen from the ground station."""
    sat = np.array(sat_xyz)
    stn = np.array(ground_xyz)
    diff = sat - stn
    rho = np.linalg.norm(diff)
    ra = np.degrees(np.arctan2(diff[1], diff[0]))
    if ra < 0:
        ra += 360.0
    dec = np.degrees(np.arcsin(diff[2] / rho))
    return ra, dec


def _during_sunset(dt_aware: datetime, location: str, lat: float, lon: float,
                   window_minutes: int = 30) -> bool:
    """Return True if dt_aware falls within the sunset window at the observatory."""
    country, city = location.split("/", 1)
    loc_info = LocationInfo(city, country, location, lat, lon)
    s = sun(loc_info.observer, date=dt_aware.date())
    start = s["sunset"] - timedelta(minutes=window_minutes)
    end   = s["sunset"] + timedelta(minutes=window_minutes)
    return start <= dt_aware <= end


# ── Main visibility API ──────────────────────────────────────────────────────

def compute_visible_satellites(
    date_str: str,
    location: str = UNSW_LOCATION,
    lat: float = UNSW_LAT,
    lon: float = UNSW_LON,
    elevation: float = UNSW_ELEVATION,
    sunset_window_minutes: int = 30,
) -> list:
    """
    Compute Starlink satellites visible from the observatory on the given date.

    Reads orbital states from the tracker's ephemeris, keeps only records that
    fall within the sunset observation window, then converts ECI->RA/Dec.
    Results are also written to VISIBILITY_FILE for reference.

    Args:
        date_str: "YYYY-MM-DD" - must match dates in the ephemeris file.
        location: pytz timezone string in "Country/City" form (e.g. "Australia/Sydney").
        lat:      Observatory latitude in degrees.
        lon:      Observatory longitude in degrees.
        elevation: Observatory elevation in metres.
        sunset_window_minutes: Minutes either side of sunset to include.

    Returns:
        List of dicts:
            [{"time": str, "satellite_id": str, "ra_deg": float, "dec_deg": float}, ...]
        Empty list when no records match, or ephemeris file is missing.
    """
    if not ECI_FILE.exists():
        raise FileNotFoundError(
            f"Ephemeris file not found: {ECI_FILE}\n"
            "Run download_starlink_eph.py from the autonomous-satellite-tracker directory."
        )

    if not extract_eci_for_date(date_str):
        return []

    tz = pytz.timezone(location)
    results = []

    with open(EPH_ECI_FILE, newline="") as f:
        for row in csv.DictReader(f):
            pos_str = row["position"].strip("()")
            x_km, y_km, z_km = map(float, pos_str.split(","))
            sat_xyz = (x_km * 1000, y_km * 1000, z_km * 1000)  # km -> m

            dt_naive = datetime.strptime(row["time"], "%Y-%m-%d %H:%M:%S")
            dt_aware = tz.localize(dt_naive)

            if not _during_sunset(dt_aware, location, lat, lon, sunset_window_minutes):
                continue

            ground_xyz = _ground_eci(lat, lon, elevation, dt_aware)
            ra, dec = _eci_to_radec(sat_xyz, ground_xyz)

            results.append({
                "time":         row["time"],
                "satellite_id": row["satellite_id"],
                "ra_deg":       ra,
                "dec_deg":      dec,
            })

    # Write visibility results CSV (mirrors ephemerisFunctions.to_radec output)
    VISIBILITY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(VISIBILITY_FILE, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Time (s)", "Satellite ID", "RA (deg)", "Dec (deg)"])
        for r in results:
            w.writerow([r["time"], r["satellite_id"], r["ra_deg"], r["dec_deg"]])

    return results


def get_visible_satellites_today(
    location: str = UNSW_LOCATION,
    lat: float = UNSW_LAT,
    lon: float = UNSW_LON,
    elevation: float = UNSW_ELEVATION,
) -> list:
    """Convenience wrapper: compute visible satellites for today's date."""
    today = datetime.now().strftime("%Y-%m-%d")
    return compute_visible_satellites(today, location, lat, lon, elevation)


def load_visibility_results() -> list:
    """
    Load the most recently written visibility_result.csv from the tracker.

    Returns:
        List of dicts with keys: time, satellite_id, ra_deg, dec_deg
        Empty list if the file doesn't exist yet.
    """
    if not VISIBILITY_FILE.exists():
        return []
    results = []
    with open(VISIBILITY_FILE, newline="") as f:
        for row in csv.DictReader(f):
            results.append({
                "time":         row["Time (s)"],
                "satellite_id": row.get("Satellite ID", "unknown"),
                "ra_deg":       float(row["RA (deg)"]),
                "dec_deg":      float(row["Dec (deg)"]),
            })
    return results


# ── Coordinate formatting helpers ────────────────────────────────────────────

def ra_deg_to_hours(ra_deg: float) -> tuple:
    """Convert RA degrees to (hours, minutes, seconds)."""
    h = ra_deg / 15.0
    hours = int(h)
    minutes = int((h - hours) * 60)
    seconds = (h - hours - minutes / 60) * 3600
    return hours, minutes, seconds


def dec_deg_to_dms(dec_deg: float) -> tuple:
    """Convert Dec degrees to (degrees, arcminutes, arcseconds)."""
    d = int(dec_deg)
    arcmin = int(abs(dec_deg - d) * 60)
    arcsec = (abs(dec_deg - d) * 60 - arcmin) * 60
    return d, arcmin, arcsec


def ra_deg_to_hours_decimal(ra_deg: float) -> float:
    """Convert RA degrees to decimal hours (for ASCOM slew_to_coordinates)."""
    return ra_deg / 15.0
