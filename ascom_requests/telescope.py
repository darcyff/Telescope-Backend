import requests

BASE_URL = "http://localhost:11111"
TELESCOPE = f"{BASE_URL}/api/v1/telescope/0"


def _get(endpoint, params=None):
    r = requests.get(f"{TELESCOPE}/{endpoint}", params=params or {})
    r.raise_for_status()
    data = r.json()
    if data.get("ErrorNumber", 0) != 0:
        raise RuntimeError(f"ASCOM error {data['ErrorNumber']}: {data['ErrorMessage']}")
    return data.get("Value")


def _put(endpoint, data=None):
    r = requests.put(f"{TELESCOPE}/{endpoint}", data=data or {})
    r.raise_for_status()
    resp = r.json()
    if resp.get("ErrorNumber", 0) != 0:
        raise RuntimeError(f"ASCOM error {resp['ErrorNumber']}: {resp['ErrorMessage']}")
    return resp.get("Value")


# Connection & Info

def get_connected():
    return _get("connected")

def set_connected(connected: bool):
    return _put("connected", {"Connected": connected})

def get_name():
    return _get("name")

def get_description():
    return _get("description")

def get_driverinfo():
    return _get("driverinfo")

def get_driverversion():
    return _get("driverversion")

def get_interfaceversion():
    return _get("interfaceversion")

def get_supportedactions():
    return _get("supportedactions")


# Capabilities

def get_alignmentmode():
    return _get("alignmentmode")

def get_equatorialsystem():
    return _get("equatorialsystem")

def get_aperturearea():
    return _get("aperturearea")

def get_aperturediameter():
    return _get("aperturediameter")

def get_focallength():
    return _get("focallength")

def get_doesrefraction():
    return _get("doesrefraction")

def get_canfindhome():
    return _get("canfindhome")

def get_canpark():
    return _get("canpark")

def get_canpulseguide():
    return _get("canpulseguide")

def get_cansetdeclinationrate():
    return _get("cansetdeclinationrate")

def get_cansetguiderates():
    return _get("cansetguiderates")

def get_cansetpark():
    return _get("cansetpark")

def get_cansetpierside():
    return _get("cansetpierside")

def get_cansetrightascensionrate():
    return _get("cansetrightascensionrate")

def get_cansettracking():
    return _get("cansettracking")

def get_canslew():
    return _get("canslew")

def get_canslewaltaz():
    return _get("canslewaltaz")

def get_canslewaltazasync():
    return _get("canslewaltazasync")

def get_canslewasync():
    return _get("canslewasync")

def get_cansync():
    return _get("cansync")

def get_cansyncaltaz():
    return _get("cansyncaltaz")

def get_canunpark():
    return _get("canunpark")

def get_canmoveaxis():
    return _get("canmoveaxis")


# ── Position ─────────────────────────────────────────────────────────

def get_rightascension():
    return _get("rightascension")

def get_declination():
    return _get("declination")

def get_altitude():
    return _get("altitude")

def get_azimuth():
    return _get("azimuth")

def get_siderealtime():
    return _get("siderealtime")

def get_sideofpier():
    return _get("sideofpier")


# ── Site Configuration ───────────────────────────────────────────────

def get_sitelatitude():
    return _get("sitelatitude")

def set_sitelatitude(latitude: float):
    return _put("sitelatitude", {"SiteLatitude": latitude})

def get_sitelongitude():
    return _get("sitelongitude")

def set_sitelongitude(longitude: float):
    return _put("sitelongitude", {"SiteLongitude": longitude})

def get_siteelevation():
    return _get("siteelevation")

def set_siteelevation(elevation: float):
    return _put("siteelevation", {"SiteElevation": elevation})


# ── State & Tracking ─────────────────────────────────────────────────

def get_tracking():
    return _get("tracking")

def set_tracking(tracking: bool):
    return _put("tracking", {"Tracking": tracking})

def get_trackingrate():
    return _get("trackingrate")

def set_trackingrate(rate: int):
    return _put("trackingrate", {"TrackingRate": rate})

def get_trackingrates():
    return _get("trackingrates")

def get_slewing():
    return _get("slewing")

def get_athome():
    return _get("athome")

def get_atpark():
    return _get("atpark")

def get_ispulseguiding():
    return _get("ispulseguiding")

def get_utcdate():
    return _get("utcdate")


# ── Target Coordinates ───────────────────────────────────────────────

def get_targetrightascension():
    return _get("targetrightascension")

def set_targetrightascension(ra: float):
    return _put("targetrightascension", {"TargetRightAscension": ra})

def get_targetdeclination():
    return _get("targetdeclination")

def set_targetdeclination(dec: float):
    return _put("targetdeclination", {"TargetDeclination": dec})


# ── Slewing (Async) ─────────────────────────────────────────────────

def slew_to_coordinates_async(ra: float, dec: float):
    return _put("slewtocoordinatesasync", {"RightAscension": ra, "Declination": dec})

def slew_to_target_async():
    return _put("slewtotargetasync")

def slew_to_altaz_async(azimuth: float, altitude: float):
    return _put("slewtoaltazasync", {"Azimuth": azimuth, "Altitude": altitude})

def abort_slew():
    return _put("abortslew")


# ── Slewing (Synchronous) ───────────────────────────────────────────

def slew_to_coordinates(ra: float, dec: float):
    return _put("slewtocoordinates", {"RightAscension": ra, "Declination": dec})

def slew_to_target():
    return _put("slewtotarget")

def slew_to_altaz(azimuth: float, altitude: float):
    return _put("slewtoaltaz", {"Azimuth": azimuth, "Altitude": altitude})


# ── Park & Home ──────────────────────────────────────────────────────

def park():
    return _put("park")

def unpark():
    return _put("unpark")

def findhome():
    return _put("findhome")


# ── Axis Motion & Guide ─────────────────────────────────────────────

def moveaxis(axis: int, rate: float):
    return _put("moveaxis", {"Axis": axis, "Rate": rate})

def get_axisrates(axis: int):
    return _get("axisrates", {"Axis": axis})

def pulseguide(direction: int, duration: int):
    return _put("pulseguide", {"Direction": direction, "Duration": duration})

def get_guideratedeclination():
    return _get("guideratedeclination")

def set_guideratedeclination(rate: float):
    return _put("guideratedeclination", {"GuideRateDeclination": rate})

def get_guideraterightascension():
    return _get("guideraterightascension")

def set_guideraterightascension(rate: float):
    return _put("guideraterightascension", {"GuideRateRightAscension": rate})


# ── Synchronization ──────────────────────────────────────────────────

def sync_to_coordinates(ra: float, dec: float):
    return _put("synctocoordinates", {"RightAscension": ra, "Declination": dec})

def sync_to_altaz(azimuth: float, altitude: float):
    return _put("synctoaltaz", {"Azimuth": azimuth, "Altitude": altitude})

def sync_to_target():
    return _put("synctotarget")


# ── Pier Side ────────────────────────────────────────────────────────

def get_destinationsideofpier(ra: float, dec: float):
    return _get("destinationsideofpier", {"RightAscension": ra, "Declination": dec})
