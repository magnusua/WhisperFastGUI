"""Shared Google Desktop OAuth: PKCE + loopback http://127.0.0.1:PORT/."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Dict
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
GOOGLE_CLOUD_CREDENTIALS_URL = "https://console.cloud.google.com/apis/credentials"
GOOGLE_CALENDAR_API_URL = (
    "https://console.cloud.google.com/apis/library/calendar-json.googleapis.com"
)
GOOGLE_GENERATIVE_LANGUAGE_API_URL = (
    "https://console.cloud.google.com/apis/library/generativelanguage.googleapis.com"
)
CLIENT_ID_ENV = "FTW_GOOGLE_OAUTH_CLIENT_ID"

CALENDAR_SCOPE = (
    "https://www.googleapis.com/auth/calendar.readonly "
    "https://www.googleapis.com/auth/userinfo.email"
)
GEMINI_SCOPE = (
    "https://www.googleapis.com/auth/generative-language.retriever "
    "https://www.googleapis.com/auth/cloud-platform "
    "https://www.googleapis.com/auth/userinfo.email"
)
GOOGLE_SCOPE = CALENDAR_SCOPE


def resolve_google_client_id(settings_id: str = "") -> str:
    raw = (settings_id or "").strip()
    if raw:
        return raw
    return (os.environ.get(CLIENT_ID_ENV) or "").strip()


def make_pkce() -> tuple:
    """RFC 7636 S256. Returns (verifier, challenge)."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def google_auth_url(
    client_id: str,
    redirect_uri: str,
    state: str = "ftw",
    code_challenge: str = "",
    scope: str = "",
) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": (scope or CALENDAR_SCOPE).strip(),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "include_granted_scopes": "true",
    }
    if code_challenge:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def _token_request(fields: Dict[str, str]) -> dict:
    body = urlencode({k: v for k, v in fields.items() if v}).encode("utf-8")
    req = Request(GOOGLE_TOKEN_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        err_body = ""
        try:
            from urllib.error import HTTPError

            if isinstance(e, HTTPError) and e.fp is not None:
                err_body = e.read().decode("utf-8", errors="replace")
                data = json.loads(err_body)
                raise RuntimeError(data.get("error_description") or data.get("error") or err_body) from e
        except RuntimeError:
            raise
        except Exception:
            pass
        raise RuntimeError(err_body or str(e)) from e


def exchange_google_code(
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    code_verifier: str = "",
) -> dict:
    fields = {
        "code": code,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    if client_secret:
        fields["client_secret"] = client_secret
    if code_verifier:
        fields["code_verifier"] = code_verifier
    return _token_request(fields)


def refresh_google_token(client_id: str, client_secret: str, refresh_token: str) -> dict:
    fields = {
        "client_id": client_id,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    if client_secret:
        fields["client_secret"] = client_secret
    return _token_request(fields)


def fetch_google_email(access_token: str) -> str:
    if not access_token:
        return ""
    req = Request(GOOGLE_USERINFO_URL)
    req.add_header("Authorization", f"Bearer {access_token}")
    try:
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return str(data.get("email") or "").strip()
    except Exception:
        return ""


def extract_oauth_code_from_url(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    if "code=" in raw:
        qs = parse_qs(urlparse(raw).query)
        return (qs.get("code") or [""])[0].strip()
    return raw


def _copy_and_open_url(url: str) -> None:
    try:
        from whisperfast.postprocess.common import copy_text_to_clipboard, open_url_in_browser

        copy_text_to_clipboard(url)
        open_url_in_browser(url)
    except Exception:
        import webbrowser

        webbrowser.open(url, new=2)


class GoogleLoopbackAuth:
    """Desktop OAuth: PKCE + ephemeral http://127.0.0.1:PORT/ (Google Desktop clients)."""

    def __init__(
        self,
        client_id: str,
        client_secret: str = "",
        scope: str = "",
        success_message: str = "Google is connected. You can close this tab.",
    ):
        self.client_id = (client_id or "").strip()
        if not self.client_id:
            raise RuntimeError("missing Google OAuth client ID")
        self.client_secret = (client_secret or "").strip()
        self.scope = (scope or CALENDAR_SCOPE).strip()
        self.success_message = success_message or "Google is connected. You can close this tab."
        self.verifier, self.challenge = make_pkce()
        self.state = secrets.token_urlsafe(16)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        self.port = int(sock.getsockname()[1])
        sock.close()
        self.redirect_uri = f"http://127.0.0.1:{self.port}/"
        self.url = google_auth_url(
            self.client_id,
            self.redirect_uri,
            state=self.state,
            code_challenge=self.challenge,
            scope=self.scope,
        )
        self._box = {"code": "", "error": "", "state": ""}
        self._closed = False
        self._server = HTTPServer(("127.0.0.1", self.port), self._make_handler())
        self._server.timeout = 0.5
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _make_handler(self):
        auth = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                qs = parse_qs(urlparse(self.path).query)
                code = (qs.get("code") or [""])[0]
                err = (qs.get("error") or [""])[0]
                st = (qs.get("state") or [""])[0]
                if not code and not err:
                    self.send_response(204)
                    self.end_headers()
                    return
                auth._box["code"] = code
                auth._box["error"] = err
                auth._box["state"] = st
                ok = bool(code) and st == auth.state
                html = (
                    "<!doctype html><html><body style='font-family:sans-serif;padding:2em'>"
                    f"<h2>FTW</h2><p>{auth.success_message}</p>"
                    "</body></html>"
                    if ok
                    else "<!doctype html><html><body><p>FTW: Google sign-in was cancelled.</p></body></html>"
                )
                self.send_response(200 if ok else 400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))

            def log_message(self, fmt, *args):
                del fmt, args

        return Handler

    def _serve(self):
        while not self._closed and not self._box["code"] and not self._box["error"]:
            try:
                self._server.handle_request()
            except Exception:
                break

    def open_browser(self) -> str:
        _copy_and_open_url(self.url)
        return self.url

    def exchange(self, code: str) -> dict:
        token = exchange_google_code(
            self.client_id,
            self.client_secret,
            code,
            self.redirect_uri,
            code_verifier=self.verifier,
        )
        email = fetch_google_email(str(token.get("access_token") or ""))
        token["email"] = email
        token["redirect_uri"] = self.redirect_uri
        return token

    def wait(self, timeout_s: float = 180) -> dict:
        deadline = time.time() + max(15.0, float(timeout_s))
        while time.time() < deadline:
            if self._box["error"]:
                raise RuntimeError(self._box["error"])
            if self._box["code"]:
                if self._box["state"] != self.state:
                    raise RuntimeError("state mismatch")
                return self.exchange(self._box["code"])
            time.sleep(0.15)
        raise RuntimeError("timeout")

    def close(self) -> None:
        self._closed = True
        try:
            self._server.server_close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
