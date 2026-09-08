import json
import os
import urllib.parse
import urllib.request

BASE_URL = os.environ.get("JARVIS_JOB_API_URL", "http://127.0.0.1:8765")


def weather(args: dict, **kwargs) -> str:
    location = (args.get("location") or "").strip()
    if not location:
        return json.dumps({"error": "location is required"})
    day = args.get("day") or "today"
    qs = urllib.parse.urlencode({"location": location, "day": day})
    token = os.environ.get("JARVIS_HUD_TOKEN", "")
    req = urllib.request.Request(
        f"{BASE_URL}/api/weather?{qs}",
        headers={"X-Jarvis-Token": token},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read().decode()
    except Exception as e:
        return json.dumps({"error": f"Jarvis server unreachable: {e}"})
