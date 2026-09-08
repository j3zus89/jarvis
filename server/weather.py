"""Fast weather lookup: Open-Meteo (free, no API key) instead of letting
Hermes web_search + web_extract a weather site — that path was taking 40+
seconds for a simple lookup. This is a direct API call, ~1-2s total,
reusing job_match's geocoder (already free/cached) for the location.
"""
from __future__ import annotations

import requests

import job_match

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather codes (Open-Meteo docs) -> short Spanish description.
_WMO_CODES = {
    0: "cielo despejado", 1: "mayormente despejado", 2: "parcialmente nublado",
    3: "nublado", 45: "niebla", 48: "niebla con escarcha",
    51: "llovizna ligera", 53: "llovizna moderada", 55: "llovizna intensa",
    56: "llovizna helada ligera", 57: "llovizna helada intensa",
    61: "lluvia ligera", 63: "lluvia moderada", 65: "lluvia intensa",
    66: "lluvia helada ligera", 67: "lluvia helada intensa",
    71: "nevada ligera", 73: "nevada moderada", 75: "nevada intensa",
    77: "granos de nieve", 80: "chubascos ligeros", 81: "chubascos moderados",
    82: "chubascos violentos", 85: "chubascos de nieve ligeros",
    86: "chubascos de nieve intensos", 95: "tormenta", 96: "tormenta con granizo ligero",
    99: "tormenta con granizo intenso",
}


def get_weather(location: str, day: str = "today") -> dict:
    """day: 'today' or 'tomorrow'. Returns a compact dict, or {'error': ...}
    if the location can't be geocoded — never raises."""
    coords = job_match.geocode(location)
    if not coords:
        return {"error": f"No pude localizar «{location}»."}
    lat, lon = coords
    try:
        resp = requests.get(
            FORECAST_URL,
            params={
                "latitude": lat, "longitude": lon,
                "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "timezone": "auto", "forecast_days": 2,
            },
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return {"error": f"El servicio de tiempo no respondió ({exc})."}

    day_idx = 0 if day != "tomorrow" else 1
    daily = data.get("daily") or {}
    current = data.get("current") or {}
    try:
        result = {
            "location": location,
            "day": "hoy" if day_idx == 0 else "mañana",
            "condition": _WMO_CODES.get(daily["weather_code"][day_idx], "condición desconocida"),
            "temp_max_c": daily["temperature_2m_max"][day_idx],
            "temp_min_c": daily["temperature_2m_min"][day_idx],
            "rain_probability_pct": daily["precipitation_probability_max"][day_idx],
        }
        if day_idx == 0 and current:
            result["current_temp_c"] = current.get("temperature_2m")
            result["feels_like_c"] = current.get("apparent_temperature")
            result["wind_kmh"] = current.get("wind_speed_10m")
            result["humidity_pct"] = current.get("relative_humidity_2m")
    except (KeyError, IndexError):
        return {"error": "Datos de tiempo incompletos para esa ubicación."}
    return result
