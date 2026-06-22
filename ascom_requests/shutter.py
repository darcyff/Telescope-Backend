import requests
import time

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


# ── Shutter Status ──────────────────────────────────────────────────
# ASCOM ShutterStatus values:
#   0 = Open, 1 = Closed, 2 = Opening, 3 = Closing, 4 = Error

# Note: shutter isn't implemented, so this uses a fake shutter status to simulate the real behaviour
MOCK_STATE = {
    "shutter_status": 1
}
def get_status():
    # return _get("shutterstatus")
    return MOCK_STATE["shutter_status"]

def open_shutter():
    # return _put("openshutter")
    MOCK_STATE["shutter_status"] = 2
    time.sleep(3)
    MOCK_STATE["shutter_status"] = 0
    return "opened"


def close_shutter():
    # return _put("closeshutter")
    MOCK_STATE["shutter_status"] = 3
    time.sleep(3)
    MOCK_STATE["shutter_status"] = 1
    return "closed"