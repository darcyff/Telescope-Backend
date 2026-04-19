"""
Dome Geometry Helper
====================
Calculates the expected dome azimuth for a given telescope pointing,
accounting for the off-center German Equatorial Mount geometry.

This implements the dome-mount synchronisation algorithm described in
Tejas Margapuram's Thesis C, Section IV.D (pp. 28-31):

  D.1  Coordinate Systems and Frame Conventions
       - Equatorial frame (sky): RA α (hours), Dec δ (degrees), LST (hours)
       - Local horizon ENU frame: (x_E, y_N, z_U)
       - Dome frame: X = East, Y = Up, Z = South, origin at dome centre

  D.2  RA/Dec → Pointing Direction
       Hour angle H = LST_rad − α_rad, normalised to [−π, π].
       ENU direction cosines:
         x_E = cos δ sin H
         y_N = cos δ cos H sin φ − sin δ cos φ
         z_U = cos δ cos H cos φ + sin δ sin φ
       Mapped to dome frame: (X, Y, Z) = (x_E, z_U, −y_N)

  D.3  Mount and Telescope Geometry Inside the Dome
       - RA axis unit vector: r̂_RA = (0, sin φ, −cos φ)
       - RA pivot position:   p_RA = (offset_ew, h_pier, −offset_ns)
       - DEC pivot position:  p_DEC = p_RA + d_RA→DEC · r̂_RA
       - DEC axis direction:  d̂_DEC = normalise(d_point × r̂_RA)
         (negated for east pier side; encodes meridian flip per D.6)
       - Optical axis origin: p_optic = p_DEC + d_DEC→scope · d̂_DEC
       - Aperture offset dir: â_ap = normalise(d̂_DEC × d_point)
       - Three sample rays: central, ±r_tube · â_ap

  D.4  Line-Sphere Intersection and Dome Exit Direction
       Ray r(t) = o + t·v̂ intersects dome sphere ‖r(t)‖² = R²_dome.
       Quadratic: t² + 2(o·v̂)t + (‖o‖² − R²) = 0.
       Take farther positive root. Average the three exit points.

  D.5  Conversion to Dome Azimuth
       θ_dome = mod360(atan2(X, −Z) · 180/π + 180°)

Physical parameters match the UNSW Observatory hardware (from alpaca_server.py):
  - Dome radius:             2.5 m
  - Mount NS offset:        -0.13 m  (south of dome centre)
  - Mount EW offset:         0.048 m (east of dome centre)
  - Mount pier height:       1.2 m
  - RA axis to Dec axis:     0.22 m
  - Dec axis to telescope:   0.18 m
  - Telescope tube radius:   0.175 m
  - Site latitude:          -33.855980°
  - Site longitude:          151.206666°
  - Site elevation:          55.0 m
"""

import numpy as np


# ── Observatory Physical Parameters ─────────────────────────────────

DOME_RADIUS = 2.5
MOUNT_OFFSET_NS = -0.13
MOUNT_OFFSET_EW = 0.048
MOUNT_PIER_HEIGHT = 1.2
POLAR_AXIS_TO_DEC_AXIS = 0.22
DEC_AXIS_TO_TELESCOPE = 0.18
TELESCOPE_RADIUS = 0.175

SITE_LATITUDE = -33.855980
SITE_LONGITUDE = 151.206666
SITE_ELEVATION = 55.0


# ── Vector Helpers ───────────────────────────────────────────────────

def _normalize(v):
    norm = np.linalg.norm(v)
    if norm == 0:
        return v
    return v / norm


def _ra_axis_vector(latitude_rad):
    """
    Unit vector of the RA axis — points toward the celestial pole.
    Thesis D.3: r̂_RA = (0, sin φ, −cos φ) in dome frame (East, Up, South).
    """
    return _normalize(np.array([0.0, np.sin(latitude_rad), -np.cos(latitude_rad)]))


def _pointing_direction(ra_hours, dec_degrees, sidereal_time_hours, latitude_rad):
    """
    Telescope pointing unit vector in the dome frame (X=East, Y=Up, Z=South).

    Thesis D.2: Standard equatorial → horizon (ENU) transform, then
    mapped to dome frame via (X, Y, Z) = (x_E, z_U, −y_N).
    """
    ra_rad = np.deg2rad(ra_hours * 15.0)
    dec_rad = np.deg2rad(dec_degrees)
    lst_rad = np.deg2rad(sidereal_time_hours * 15.0)
    ha_rad = lst_rad - ra_rad
    # Normalise hour angle to [−π, π] for numerical stability
    ha_rad = (ha_rad + np.pi) % (2 * np.pi) - np.pi

    sin_dec = np.sin(dec_rad)
    cos_dec = np.cos(dec_rad)
    sin_lat = np.sin(latitude_rad)
    cos_lat = np.cos(latitude_rad)
    sin_ha = np.sin(ha_rad)
    cos_ha = np.cos(ha_rad)

    # ENU direction cosines (Thesis D.2)
    x_east = cos_dec * sin_ha
    y_north = cos_dec * cos_ha * sin_lat - sin_dec * cos_lat
    z_up = cos_dec * cos_ha * cos_lat + sin_dec * sin_lat

    # ENU → dome frame: (X=East, Y=Up, Z=South) = (x_E, z_U, −y_N)
    return _normalize(np.array([x_east, z_up, -y_north]))


def _line_sphere_intersection(origin, direction, radius):
    """
    Intersection of ray (origin + t * direction) with sphere of given radius.

    Thesis D.4: quadratic t² + 2(o·v̂)t + (‖o‖² − R²) = 0.
    Returns the farther positive intersection point, or origin if none.
    """
    direction = _normalize(direction)
    b = 2.0 * np.dot(origin, direction)
    c = np.dot(origin, origin) - radius ** 2
    discriminant = b ** 2 - 4 * c
    if discriminant < 0:
        return origin
    sqrt_disc = np.sqrt(discriminant)
    t = max((-b + sqrt_disc) / 2.0, (-b - sqrt_disc) / 2.0)
    if t < 0:
        return origin
    return origin + direction * t


# ── Main Calculation ─────────────────────────────────────────────────

def expected_dome_azimuth(ra_hours, dec_degrees, sidereal_time_hours, pier_side):
    """
    Calculate the dome azimuth the server's slaving loop should command,
    given the telescope's current RA, Dec, sidereal time, and pier side.

    Implements the full algorithm from Thesis Sections D.2 through D.5.

    Args:
        ra_hours:             Right ascension in decimal hours [0, 24)
        dec_degrees:          Declination in decimal degrees [-90, 90]
        sidereal_time_hours:  Local sidereal time in decimal hours [0, 24)
        pier_side:            "east" or "west" (or 0/1 int: 0=east, 1=west)

    Returns:
        Expected dome azimuth in degrees [0, 360)
    """
    latitude_rad = np.deg2rad(SITE_LATITUDE)

    # D.2 — pointing direction in dome frame
    pointing_dir = _pointing_direction(ra_hours, dec_degrees, sidereal_time_hours, latitude_rad)

    # D.3 — RA axis vector (points to celestial pole)
    ra_axis_vec = _ra_axis_vector(latitude_rad)

    # D.3 — RA pivot in dome frame: (offset_ew, h_pier, −offset_ns)
    ra_pivot_pos = np.array([
        MOUNT_OFFSET_EW,
        MOUNT_PIER_HEIGHT,
        -MOUNT_OFFSET_NS,
    ])

    # D.3 — DEC pivot: p_DEC = p_RA + d_RA→DEC · r̂_RA
    dec_pivot_pos = ra_pivot_pos + ra_axis_vec * POLAR_AXIS_TO_DEC_AXIS

    # D.3 — DEC axis direction: d̂_DEC = normalise(d_point × r̂_RA)
    dec_axis_dir = _normalize(np.cross(pointing_dir, ra_axis_vec))
    if np.linalg.norm(dec_axis_dir) < 1e-6:
        fallback = np.array([1.0, 0.0, 0.0])
        dec_axis_dir = _normalize(np.cross(pointing_dir, fallback))

    # D.6 — Pier side: negate d̂_DEC for east side (meridian flip encoding)
    if isinstance(pier_side, int):
        is_east = pier_side == 0
    else:
        is_east = str(pier_side).lower().startswith("e")

    if is_east:
        dec_axis_dir = -dec_axis_dir

    # D.3 — Optical axis origin: p_optic = p_DEC + d_DEC→scope · d̂_DEC
    optic_origin = dec_pivot_pos + dec_axis_dir * DEC_AXIS_TO_TELESCOPE

    # D.3 — Aperture offset direction: â_ap = normalise(d̂_DEC × d_point)
    aperture_dir = _normalize(np.cross(dec_axis_dir, pointing_dir))
    if np.linalg.norm(aperture_dir) < 1e-6:
        aperture_dir = _normalize(np.cross(pointing_dir, ra_axis_vec))

    # D.4 — Three sample rays: central + edges at ±r_tube · â_ap
    exit_points = [_line_sphere_intersection(optic_origin, pointing_dir, DOME_RADIUS)]
    if TELESCOPE_RADIUS > 0:
        offset = aperture_dir * TELESCOPE_RADIUS
        exit_points.append(_line_sphere_intersection(optic_origin + offset, pointing_dir, DOME_RADIUS))
        exit_points.append(_line_sphere_intersection(optic_origin - offset, pointing_dir, DOME_RADIUS))

    # D.4 — Average exit points to get mean dome intersection direction
    avg_vector = _normalize(np.mean(exit_points, axis=0))

    # D.5 — Convert to dome azimuth: θ = mod360(atan2(X, −Z) · 180/π + 180°)
    az_rad = np.arctan2(avg_vector[0], -avg_vector[2])
    az_deg = (np.rad2deg(az_rad) + 180) % 360.0

    return az_deg


def azimuth_difference(az1, az2):
    """
    Shortest angular difference between two azimuths in degrees.
    Always returns a positive value in [0, 180].
    """
    diff = abs(az1 - az2) % 360
    if diff > 180:
        diff = 360 - diff
    return diff
