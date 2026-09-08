"""Handlers: HTTP to the Jarvis voice server's /api/job_profile and
/api/job_match (same loopback-call pattern as hud_display/tools.py)."""
import json
import os
import urllib.request

BASE_URL = os.environ.get("JARVIS_JOB_API_URL", "http://127.0.0.1:8765")


def _call(method: str, path: str, body: dict | None = None) -> str:
    token = os.environ.get("JARVIS_HUD_TOKEN", "")
    req = urllib.request.Request(
        BASE_URL + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Content-Type": "application/json", "X-Jarvis-Token": token},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode()
    except urllib.error.HTTPError as e:
        return json.dumps({"error": f"HTTP {e.code}: {e.read().decode(errors='replace')}"})
    except Exception as e:
        return json.dumps({"error": f"Jarvis server unreachable: {e}"})


def job_profile(args: dict, **kwargs) -> str:
    return _call("GET", "/api/job_profile")


def job_match(args: dict, **kwargs) -> str:
    postings = args.get("postings")
    if not isinstance(postings, list) or not postings:
        return json.dumps({"error": "postings (non-empty list) is required"})
    return _call("POST", "/api/job_match", {"postings": postings})
