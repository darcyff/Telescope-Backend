import requests

BASE_URL = "http://localhost:11111"
DOME = f"{BASE_URL}/api/v1/dome/0"


def _get(endpoint, params=None):
    r = requests.get(f"{DOME}/{endpoint}", params=params or {})
    r.raise_for_status()
    data = r.json()
    if data.get("ErrorNumber", 0) != 0:
        raise RuntimeError(f"ASCOM error {data['ErrorNumber']}: {data['ErrorMessage']}")
    return data.get("Value")


def _put(endpoint, data=None):
    r = requests.put(f"{DOME}/{endpoint}", data=data or {})
    r.raise_for_status()
    resp = r.json()
    if resp.get("ErrorNumber", 0) != 0:
        raise RuntimeError(f"ASCOM error {resp['ErrorNumber']}: {resp['ErrorMessage']}")
    return resp.get("Value")


# ── Connection & Info ────────────────────────────────────────────────

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


# ── Capabilities ─────────────────────────────────────────────────────

def get_canfindhome():
    return _get("canfindhome")

def get_canpark():
    return _get("canpark")

def get_cansetaltitude():
    return _get("cansetaltitude")

def get_cansetazimuth():
    return _get("cansetazimuth")

def get_cansetpark():
    return _get("cansetpark")

def get_cansetshutter():
    return _get("cansetshutter")

def get_canslave():
    return _get("canslave")

def get_cansyncazimuth():
    return _get("cansyncazimuth")


# ── Properties ───────────────────────────────────────────────────────

def get_azimuth():
    return _get("azimuth")

def get_athome():
    return _get("athome")

def get_atpark():
    return _get("atpark")

def get_shutterstatus():
    return _get("shutterstatus")

def get_slaved():
    return _get("slaved")

def set_slaved(slaved: bool):
    return _put("slaved", {"Slaved": slaved})

def get_slewing():
    return _get("slewing")


# ── Methods ──────────────────────────────────────────────────────────

def slew_to_azimuth(azimuth: float):
    return _put("slewtoazimuth", {"Azimuth": azimuth})

def sync_to_azimuth(azimuth: float):
    return _put("synctoazimuth", {"Azimuth": azimuth})

def abort_slew():
    return _put("abortslew")

def findhome():
    return _put("findhome")

