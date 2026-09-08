"""One-off recovery: ~/.hermes/google_token.json's refresh_token was rejected
by Google (selfcheck: "Google rechazo renovar Gmail"), so nothing short of a
fresh consent will fix it. Redoes the same OAuth2 loopback flow Google
already granted before, with the exact same scopes, so nothing else that
already depended on this token (calendar, docs, drive, sheets) regresses.

Plain REST OAuth2 (stdlib + requests only) -- matches the style
server.py's own _gmail_access_token() already uses for token refresh, no new
dependency needed.

Usage: .venv/Scripts/python.exe scripts/relink_google.py
Opens your real browser; log into the same Google account as before and
accept. Needs ~/.hermes/google_client_secret.json (already present).
"""
import http.server
import json
import secrets
import urllib.parse
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

HERMES = Path.home() / ".hermes"
CLIENT_SECRET_FILE = HERMES / "google_client_secret.json"
TOKEN_FILE = HERMES / "google_token.json"

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/contacts.readonly",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/spreadsheets",
]


def main() -> None:
    client = json.loads(CLIENT_SECRET_FILE.read_text(encoding="utf-8"))["installed"]
    client_id, client_secret = client["client_id"], client["client_secret"]
    auth_uri, token_uri = client["auth_uri"], client["token_uri"]

    state = secrets.token_urlsafe(16)
    result: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            result["code"] = qs.get("code", [None])[0]
            result["state"] = qs.get("state", [None])[0]
            result["error"] = qs.get("error", [None])[0]
            body = "Listo, ya puedes cerrar esta pestana y volver a la terminal.".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    redirect_uri = f"http://localhost:{port}/"

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    url = f"{auth_uri}?{urllib.parse.urlencode(params)}"
    print("Abriendo el navegador para iniciar sesion en Google (misma cuenta de antes)...")
    print(url)
    webbrowser.open(url)

    print("Esperando el login en el navegador (timeout 180s)...")
    server.timeout = 180
    server.handle_request()

    if result.get("error"):
        raise SystemExit(f"Google devolvio un error: {result['error']}")
    if not result.get("code"):
        raise SystemExit("No llego ningun codigo de autorizacion (timeout o cancelado).")
    if result.get("state") != state:
        raise SystemExit("El 'state' no coincide, abortado por seguridad.")

    resp = requests.post(token_uri, data={
        "code": result["code"],
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }, timeout=20)
    resp.raise_for_status()
    tok = resp.json()
    if not tok.get("refresh_token"):
        raise SystemExit(
            "Google no devolvio refresh_token. Revoca el acceso previo en "
            "https://myaccount.google.com/permissions y vuelve a intentarlo."
        )

    expiry = (
        datetime.now(timezone.utc) + timedelta(seconds=int(tok.get("expires_in") or 3500))
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_token = {
        "token": tok["access_token"],
        "refresh_token": tok["refresh_token"],
        "token_uri": token_uri,
        "client_id": client_id,
        "client_secret": client_secret,
        "scopes": SCOPES,
        "universe_domain": "googleapis.com",
        "account": "",
        "expiry": expiry,
        "type": "authorized_user",
    }
    TOKEN_FILE.write_text(json.dumps(new_token, indent=2), encoding="utf-8")
    print(f"Listo: token nuevo guardado en {TOKEN_FILE}")


if __name__ == "__main__":
    main()
