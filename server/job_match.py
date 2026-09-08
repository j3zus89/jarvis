"""JOB_AGENT matching engine: profile loading, dedupe, geocode/distance, and
compatibility scoring for job postings. Pure Python, zero LLM tokens —
Hermes finds the postings with its own web search; this module only
normalizes and ranks what it already found. See docs/instrucciones
herrameintas.md for the spec this implements.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from pathlib import Path
from typing import Any

import requests
import yaml

PROFILE_PATH = Path.home() / ".hermes" / "memories" / "job_profile.yaml"
GEOCODE_CACHE_PATH = Path(__file__).parent / "logs" / "geocode_cache.json"

PROFILE_TEMPLATE = """\
# Perfil para JOB_AGENT (server/job_match.py). Rellena lo que quieras usar;
# los campos vacios o en null se ignoran al buscar/filtrar, nunca bloquean
# una busqueda. No se envia entero al modelo: cada busqueda solo usa los
# campos que le hacen falta para filtrar/puntuar.
location: ""              # tu ciudad base, ej: "Alsasua, Navarra, España"
radius_km: 30              # radio maximo de busqueda en km
desired_roles: []          # ej: ["tecnico informatico", "soporte tecnico"]
experience_years: null     # ej: 5
skills: []                 # ej: ["Python", "redes", "Windows Server"]
education: ""              # ej: "FP Grado Superior en Administracion de Sistemas"
languages: []              # ej: ["español (nativo)", "ingles (B1)"]
salary_min: null           # eur brutos/año, ej: 22000
schedule: ""                # ej: "jornada completa", "media jornada", "indiferente"
contract_types: []         # ej: ["indefinido", "temporal"]
transport:
  has_vehicle: false
  public_transport_ok: true
preferences: []            # ej: ["remoto o hibrido", "turno de mañana"]
restrictions: []           # ej: ["no turnos de noche"]
"""

_DEFAULT_PROFILE: dict[str, Any] = {
    "location": "",
    "radius_km": None,
    "desired_roles": [],
    "experience_years": None,
    "skills": [],
    "education": "",
    "languages": [],
    "salary_min": None,
    "schedule": "",
    "contract_types": [],
    "transport": {"has_vehicle": False, "public_transport_ok": True},
    "preferences": [],
    "restrictions": [],
}


def ensure_profile_file() -> None:
    if PROFILE_PATH.exists():
        return
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(PROFILE_TEMPLATE, encoding="utf-8")


def load_profile() -> dict[str, Any]:
    ensure_profile_file()
    try:
        raw = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        raw = {}
    profile = dict(_DEFAULT_PROFILE)
    profile.update({k: v for k, v in raw.items() if k in _DEFAULT_PROFILE})
    transport = dict(_DEFAULT_PROFILE["transport"])
    if isinstance(raw.get("transport"), dict):
        transport.update(raw["transport"])
    profile["transport"] = transport
    return profile


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _load_geocode_cache() -> dict[str, list[float] | None]:
    try:
        return json.loads(GEOCODE_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_geocode_cache(cache: dict) -> None:
    try:
        GEOCODE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        GEOCODE_CACHE_PATH.write_text(json.dumps(cache), encoding="utf-8")
    except Exception:
        pass


def geocode(place: str) -> tuple[float, float] | None:
    """City/address -> (lat, lon) via Nominatim (OSM), no key required.
    Cached on disk — geocoding the same handful of cities repeatedly would
    be wasteful and Nominatim's usage policy asks for restraint (~1 req/s)."""
    place = (place or "").strip()
    if not place:
        return None
    cache = _load_geocode_cache()
    key = _norm(place)
    if key in cache:
        val = cache[key]
        return tuple(val) if val else None
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": place, "format": "json", "limit": 1},
            headers={"User-Agent": "jarvis-hud-hermes/job_match (personal assistant)"},
            timeout=8,
        )
        resp.raise_for_status()
        results = resp.json()
        coords = (float(results[0]["lat"]), float(results[0]["lon"])) if results else None
    except Exception:
        coords = None
    cache[key] = list(coords) if coords else None
    _save_geocode_cache(cache)
    return coords


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, [*a, *b])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def _dedupe(postings: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for p in postings:
        url = (p.get("url") or "").split("?")[0].rstrip("/")
        key = url or f"{_norm(p.get('title', ''))}|{_norm(p.get('company', ''))}"
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


_VEHICLE_RE = re.compile(
    r"veh[ií]culo propio|coche propio|carn[eé]t de conducir|permiso de conducir|own vehicle|driver'?s licen[sc]e",
    re.I,
)
_PUBLIC_TRANSPORT_RE = re.compile(r"transporte p[uú]blico|public transport", re.I)
_NEGATION_PREFIX_RE = re.compile(r"^(?:no|sin)\s+", re.I)


def _phrase_variants(s: str) -> list[str]:
    """'remoto o híbrido' -> ['remoto', 'híbrido']. Free-text preferences and
    restrictions are written by the user, not a fixed vocabulary — splitting
    on the obvious separators catches more real postings than one literal
    substring match would."""
    parts = re.split(r"\s+o\s+|\s+u\s+|,\s*|/", s.strip(), flags=re.I)
    return [p.strip().lower() for p in parts if p.strip()]


def _parse_salary(text: str | None) -> float | None:
    if not text:
        return None
    m = re.search(r"(\d{1,3}(?:[.,]\d{3})*)\s*(k\b)?", text, re.I)
    if not m:
        return None
    num = float(m.group(1).replace(".", "").replace(",", ""))
    if m.group(2):
        num *= 1000
    return num


def _score_one(p: dict, profile: dict) -> dict:
    title = (p.get("title") or "").lower()
    text = " ".join(str(p.get(k, "")) for k in ("title", "description", "requirements")).lower()

    distance_km: float | None = None
    if profile["location"] and p.get("location"):
        home = geocode(profile["location"])
        there = geocode(p["location"])
        if home and there:
            distance_km = round(_haversine_km(home, there), 1)
            if profile["radius_km"] and distance_km > float(profile["radius_km"]):
                return {"discard": f"a {distance_km:.0f} km, fuera de tu radio de {profile['radius_km']} km",
                        "distance_km": distance_km}

    requires_vehicle = bool(_VEHICLE_RE.search(text))
    if requires_vehicle and not profile["transport"]["has_vehicle"]:
        rescued = bool(_PUBLIC_TRANSPORT_RE.search(text)) and profile["transport"]["public_transport_ok"]
        if not rescued:
            return {"discard": "requiere vehículo propio", "distance_km": distance_km}

    salary_val = _parse_salary(p.get("salary"))
    if salary_val and profile["salary_min"] and salary_val < float(profile["salary_min"]):
        return {"discard": f"salario ~{salary_val:.0f}€ por debajo de tu mínimo ({profile['salary_min']}€)",
                "distance_km": distance_km}

    for restriction in profile.get("restrictions") or []:
        phrase = _NEGATION_PREFIX_RE.sub("", restriction.strip())
        if any(v and v in text for v in _phrase_variants(phrase)):
            return {"discard": f"no cumple tu restricción: {restriction}", "distance_km": distance_km}

    reasons: list[str] = []
    missing: list[str] = []
    score = 50.0
    if profile["desired_roles"]:
        roles = [r.lower() for r in profile["desired_roles"]]
        if any(r in title for r in roles):
            score += 25
            reasons.append("el puesto coincide con lo que buscas")
        elif any(r in text for r in roles):
            score += 10
        else:
            missing.append("el puesto no menciona claramente tu rol buscado")
    if profile["skills"]:
        matched = [s for s in profile["skills"] if s.lower() in text]
        if matched:
            score += min(20, 5 * len(matched))
            reasons.append("coincide en: " + ", ".join(matched[:4]))
    if distance_km is not None:
        if distance_km <= 10:
            score += 10
            reasons.append(f"muy cerca ({distance_km:.0f} km)")
        else:
            reasons.append(f"dentro de tu radio ({distance_km:.0f} km)")
    if requires_vehicle and profile["transport"]["public_transport_ok"]:
        reasons.append("menciona vehículo pero es accesible en transporte público")
    matched_prefs = [
        pref for pref in (profile.get("preferences") or [])
        if any(v and v in text for v in _phrase_variants(pref))
    ]
    if matched_prefs:
        score += min(15, 5 * len(matched_prefs))
        reasons.append("coincide con tu preferencia: " + ", ".join(matched_prefs[:2]))
    contract = (p.get("contract") or "").lower()
    if profile["contract_types"] and contract:
        if any(c.lower() in contract for c in profile["contract_types"]):
            score += 5
        else:
            missing.append(f"contrato distinto al preferido ({p.get('contract')})")
    schedule_pref = (profile.get("schedule") or "").strip().lower()
    posting_schedule = (p.get("schedule") or "").lower()
    if schedule_pref and schedule_pref != "indiferente" and posting_schedule:
        if any(v in posting_schedule for v in _phrase_variants(schedule_pref)):
            score += 5
            reasons.append(f"jornada como la prefieres ({p.get('schedule')})")
        else:
            missing.append(f"jornada distinta a la preferida ({p.get('schedule')})")
    if profile.get("education"):
        edu_words = [w for w in re.findall(r"[a-záéíóúñ]{4,}", profile["education"].lower())
                     if w not in ("grado", "superior", "formacion", "profesional")]
        if edu_words and any(w in text for w in edu_words):
            score += 5
            reasons.append("tu formación encaja con lo pedido")
    if profile["experience_years"] is not None:
        m = re.search(r"(\d+)\s*(?:a[ñn]os|years)", text)
        if m and int(m.group(1)) > int(profile["experience_years"]):
            missing.append(f"pide {m.group(1)} años de experiencia")

    return {
        "score": round(max(0.0, min(100.0, score))),
        "reasons": reasons,
        "missing": missing,
        "distance_km": distance_km,
    }


def match_jobs(postings: list[dict], profile: dict | None = None, limit: int = 10) -> dict:
    """postings: list of dicts with at least title+url; company, location,
    salary, contract, schedule, description, requirements are optional —
    Hermes passes whatever it actually found via its own web search."""
    profile = profile or load_profile()
    deduped = _dedupe([p for p in postings if p.get("title")])

    results = []
    discarded = []
    for p in deduped:
        outcome = _score_one(p, profile)
        base = {
            "title": p.get("title", ""),
            "company": p.get("company", ""),
            "location": p.get("location", ""),
            "distance_km": outcome.get("distance_km"),
            "salary": p.get("salary", ""),
            "contract": p.get("contract", ""),
            "schedule": p.get("schedule", ""),
            "source": p.get("source", ""),
            "url": p.get("url", ""),
        }
        if "discard" in outcome:
            discarded.append({**base, "reason": outcome["discard"]})
        else:
            results.append({
                **base,
                "compatibility_score": outcome["score"],
                "matching_reasons": outcome["reasons"],
                "missing_requirements": outcome["missing"],
            })

    results.sort(key=lambda r: r["compatibility_score"], reverse=True)
    return {
        "results": results[:limit],
        "total_found": len(deduped),
        "total_matched": len(results),
        "discarded": discarded,
    }
