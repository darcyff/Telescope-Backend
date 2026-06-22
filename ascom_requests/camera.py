import os
import requests

BASE_URL = "http://localhost:11111"
CAMERA = f"{BASE_URL}/api/v1/camera/0"


def _get(endpoint, params=None):
    r = requests.get(f"{CAMERA}/{endpoint}", params=params or {})
    r.raise_for_status()
    data = r.json()
    if data.get("ErrorNumber", 0) != 0:
        raise RuntimeError(f"ASCOM error {data['ErrorNumber']}: {data['ErrorMessage']}")
    return data.get("Value")


def _put(endpoint, data=None):
    r = requests.put(f"{CAMERA}/{endpoint}", data=data or {})
    r.raise_for_status()
    resp = r.json()
    if resp.get("ErrorNumber", 0) != 0:
        raise RuntimeError(f"ASCOM error {resp['ErrorNumber']}: {resp['ErrorMessage']}")
    return resp.get("Value")


# ── Connection ──────────────────────────────────────────────────────

def get_connected():
    return _get("connected")

def set_connected(connected: bool):
    return _put("connected", {"Connected": connected})


# ── Exposure State ──────────────────────────────────────────────────

def get_camera_state():
    return _get("camerastate")

def get_image_ready():
    return _get("imageready")


# ── Cooling ─────────────────────────────────────────────────────────

def get_ccd_temperature():
    return _get("ccdtemperature")

def set_ccd_temperature(temp: float):
    return _put("setccdtemperature", {"SetCCDTemperature": temp})

def get_cooler_on():
    return _get("cooleron")

def set_cooler_on(on: bool):
    return _put("cooleron", {"CoolerOn": on})

def get_cooler_power():
    return _get("coolerpower")


# ── Gain & Offset ───────────────────────────────────────────────────

def get_gain():
    return _get("gain")

def set_gain(gain: int):
    return _put("gain", {"Gain": gain})

def get_offset():
    return _get("offset")

def set_offset(offset: int):
    return _put("offset", {"Offset": offset})


# ── Capture (non-ASCOM extension) ──────────────────────────────────

def capture(duration: float, save_path: str, light: bool = True):
    """Expose and download the resulting FITS file to save_path."""
    r = requests.put(
        f"{CAMERA}/capture",
        data={"Duration": duration, "Light": light},
        stream=True,
    )
    r.raise_for_status()

    content_type = r.headers.get("content-type", "")
    if "application/json" in content_type:
        resp = r.json()
        if resp.get("ErrorNumber", 0) != 0:
            raise RuntimeError(f"ASCOM error {resp['ErrorNumber']}: {resp['ErrorMessage']}")

    os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
    with open(save_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            f.write(chunk)
