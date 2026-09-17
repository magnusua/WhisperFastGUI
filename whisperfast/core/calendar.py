"""Meeting calendar: ICS file/URL, Outlook COM, Google Calendar OAuth (optional)."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from whisperfast.secrets_store import protect_string, unprotect_string

MEETING_URL_HINTS = (
    "meet.google.com",
    "zoom.us",
    "teams.microsoft.com",
    "teams.live.com",
    "meet.google",
)


def parse_ics(text: str) -> List[dict]:
    """Parse VEVENT blocks from an ICS document (unfolded lines)."""
    if not text:
        return []
    lines = _unfold_ics(text)
    events = []
    cur: Dict[str, str] = {}
    in_event = False
    for line in lines:
        if line == "BEGIN:VEVENT":
            in_event = True
            cur = {}
            continue
        if line == "END:VEVENT":
            if cur:
                events.append(_ics_event(cur))
            in_event = False
            cur = {}
            continue
        if not in_event or ":" not in line:
            continue
        key, val = line.split(":", 1)
        key = key.split(";", 1)[0].upper()
        if key == "ATTENDEE":
            cur[key] = (cur.get(key) or "") + (";" if cur.get(key) else "") + val
        else:
            cur[key] = val
    return [e for e in events if e.get("title") or e.get("start")]


def _unfold_ics(text: str) -> List[str]:
    raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: List[str] = []
    for line in raw:
        if line.startswith(" ") or line.startswith("\t"):
            if out:
                out[-1] += line[1:]
            continue
        out.append(line.strip())
    return out


def _ics_event(fields: Dict[str, str]) -> dict:
    start = _parse_ics_dt(fields.get("DTSTART") or "")
    end = _parse_ics_dt(fields.get("DTEND") or "")
    attendees = []
    for part in (fields.get("ATTENDEE") or "").split(";"):
        m = re.search(r"mailto:([^;]+)", part, re.I)
        if m:
            attendees.append(m.group(1).strip())
        elif part.strip():
            attendees.append(part.strip())
    return {
        "title": _unescape_ics(fields.get("SUMMARY") or ""),
        "start": start,
        "end": end,
        "attendees": attendees,
        "location": _unescape_ics(fields.get("LOCATION") or ""),
        "description": _unescape_ics(fields.get("DESCRIPTION") or ""),
        "source": "ics",
    }


def _unescape_ics(value: str) -> str:
    return (
        (value or "")
        .replace("\\n", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def _parse_ics_dt(value: str) -> Optional[datetime]:
    value = (value or "").strip()
    if not value:
        return None
    value = value.split(":", 1)[-1]
    tz = timezone.utc if value.endswith("Z") else None
    value = value.rstrip("Z")
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M", "%Y%m%d"):
        try:
            dt = datetime.strptime(value, fmt)
            if tz:
                dt = dt.replace(tzinfo=tz)
            return dt
        except ValueError:
            continue
    return None


def load_ics_path(path: str) -> List[dict]:
    if not path:
        return []
    if path.lower().startswith(("http://", "https://")):
        try:
            with urlopen(path, timeout=15) as resp:
                text = resp.read().decode("utf-8", errors="replace")
            return parse_ics(text)
        except Exception:
            return []
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return parse_ics(f.read())
    except OSError:
        return []


def event_has_meeting_url(event: dict) -> bool:
    blob = " ".join(
        [
            str(event.get("title") or ""),
            str(event.get("location") or ""),
            str(event.get("description") or ""),
            str(event.get("hangoutLink") or ""),
        ]
    ).lower()
    return any(h in blob for h in MEETING_URL_HINTS)


def event_matches_keywords(event: dict, keywords: str) -> bool:
    words = [w.strip().casefold() for w in (keywords or "").split(",") if w.strip()]
    if not words:
        return True
    title = str(event.get("title") or "").casefold()
    return any(w in title for w in words)


def filter_events(events: Iterable[dict], mode: str, keywords: str = "") -> List[dict]:
    mode = (mode or "all").strip().lower()
    out = []
    for ev in events or []:
        if mode == "meeting_url" and not event_has_meeting_url(ev):
            continue
        if mode == "keywords" and not event_matches_keywords(ev, keywords):
            continue
        out.append(ev)
    return out


def _as_aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return dt


def overlapping_event(events: Iterable[dict], when: Optional[datetime] = None) -> Optional[dict]:
    when = _as_aware(when or datetime.now().astimezone())
    best = None
    for ev in events or []:
        start = _as_aware(ev.get("start"))
        end = _as_aware(ev.get("end"))
        if not start:
            continue
        if end is None:
            end = start + timedelta(hours=1)
        if start <= when <= end:
            if best is None or start > _as_aware(best.get("start")):
                best = ev
    return best


def upcoming_event(
    events: Iterable[dict],
    when: Optional[datetime] = None,
    lead_min: int = 2,
) -> Optional[dict]:
    when = _as_aware(when or datetime.now().astimezone())
    horizon = when + timedelta(minutes=max(0, int(lead_min)))
    best = None
    for ev in events or []:
        start = _as_aware(ev.get("start"))
        end = _as_aware(ev.get("end"))
        if not start:
            continue
        if end is None:
            end = start + timedelta(hours=1)
        if start <= horizon and end >= when:
            if best is None or start < _as_aware((best or {}).get("start")):
                best = ev
    return best


def load_outlook_events(hours: int = 12) -> List[dict]:
    if os.name != "nt":
        return []
    try:
        import win32com.client  # type: ignore
    except ImportError:
        return []
    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        ns = outlook.GetNamespace("MAPI")
        folder = ns.GetDefaultFolder(9)  # olFolderCalendar
        items = folder.Items
        items.Sort("[Start]")
        items.IncludeRecurrences = True
        now = datetime.now()
        end = now + timedelta(hours=hours)
        restriction = (
            f"[Start] <= '{end.strftime('%m/%d/%Y %H:%M')}' AND "
            f"[End] >= '{now.strftime('%m/%d/%Y %H:%M')}'"
        )
        restricted = items.Restrict(restriction)
        out = []
        for item in restricted:
            try:
                out.append(
                    {
                        "title": str(getattr(item, "Subject", "") or ""),
                        "start": getattr(item, "Start", None),
                        "end": getattr(item, "End", None),
                        "attendees": [],
                        "location": str(getattr(item, "Location", "") or ""),
                        "description": str(getattr(item, "Body", "") or "")[:2000],
                        "source": "outlook",
                    }
                )
            except Exception:
                continue
        return out
    except Exception:
        return []


GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/{cal}/events"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
GOOGLE_SCOPE = (
    "https://www.googleapis.com/auth/calendar.readonly "
    "https://www.googleapis.com/auth/userinfo.email"
)
GOOGLE_CLOUD_CREDENTIALS_URL = "https://console.cloud.google.com/apis/credentials"
GOOGLE_CALENDAR_API_URL = (
    "https://console.cloud.google.com/apis/library/calendar-json.googleapis.com"
)
CLIENT_ID_ENV = "FTW_GOOGLE_OAUTH_CLIENT_ID"


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
) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": GOOGLE_SCOPE,
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

    def __init__(self, client_id: str, client_secret: str = ""):
        self.client_id = (client_id or "").strip()
        if not self.client_id:
            raise RuntimeError("missing Google OAuth client ID")
        self.client_secret = (client_secret or "").strip()
        self.verifier, self.challenge = make_pkce()
        self.state = secrets.token_urlsafe(16)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        self.port = int(sock.getsockname()[1])
        sock.close()
        self.redirect_uri = f"http://127.0.0.1:{self.port}/"
        self.url = google_auth_url(
            self.client_id, self.redirect_uri, state=self.state, code_challenge=self.challenge
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
                    "<h2>FTW</h2><p>Google Calendar is connected. You can close this tab.</p>"
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


def run_google_browser_login(
    client_id: str,
    client_secret: str = "",
    timeout_s: float = 180,
) -> dict:
    """Open the system browser, copy the auth URL (Cursor-style), wait for the loopback code."""
    session = GoogleLoopbackAuth(client_id, client_secret)
    try:
        session.open_browser()
        return session.wait(timeout_s)
    finally:
        session.close()


def fetch_google_events(
    access_token: str,
    calendar_id: str = "primary",
    hours: int = 12,
) -> List[dict]:
    now = datetime.now(timezone.utc)
    params = urlencode(
        {
            "timeMin": now.isoformat().replace("+00:00", "Z"),
            "timeMax": (now + timedelta(hours=hours)).isoformat().replace("+00:00", "Z"),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": 50,
        }
    )
    url = GOOGLE_EVENTS_URL.format(cal=calendar_id) + "?" + params
    req = Request(url)
    req.add_header("Authorization", f"Bearer {access_token}")
    with urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    out = []
    for item in data.get("items") or []:
        start = _parse_google_dt((item.get("start") or {}).get("dateTime") or (item.get("start") or {}).get("date"))
        end = _parse_google_dt((item.get("end") or {}).get("dateTime") or (item.get("end") or {}).get("date"))
        attendees = [
            a.get("email") for a in (item.get("attendees") or []) if a.get("email")
        ]
        out.append(
            {
                "title": item.get("summary") or "",
                "start": start,
                "end": end,
                "attendees": attendees,
                "location": item.get("location") or "",
                "description": item.get("description") or "",
                "hangoutLink": item.get("hangoutLink") or "",
                "source": "google",
            }
        )
    return out


def _parse_google_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    value = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None


_cache_lock = threading.Lock()
_cache: Dict[str, Any] = {"ts": 0.0, "events": []}


def collect_events(settings: Dict[str, Any], force: bool = False) -> List[dict]:
    now = time.time()
    with _cache_lock:
        if not force and _cache["events"] and now - float(_cache["ts"] or 0) < 60:
            return list(_cache["events"])
    events: List[dict] = []
    if settings.get("calendar_ics_enabled"):
        events.extend(load_ics_path(str(settings.get("calendar_ics_path") or "")))
    if settings.get("calendar_outlook_enabled"):
        events.extend(load_outlook_events())
    if settings.get("calendar_google_enabled"):
        events.extend(_google_events_from_settings(settings))
    filtered = filter_events(
        events,
        str(settings.get("calendar_event_filter") or "all"),
        str(settings.get("calendar_keywords") or ""),
    )
    with _cache_lock:
        _cache["ts"] = now
        _cache["events"] = filtered
    return list(filtered)


def _google_events_from_settings(settings: Dict[str, Any]) -> List[dict]:
    client_id = resolve_google_client_id(str(settings.get("google_calendar_client_id") or ""))
    secret = unprotect_string(str(settings.get("google_calendar_client_secret") or ""))
    refresh = unprotect_string(str(settings.get("google_calendar_refresh_token") or ""))
    if not client_id or not refresh:
        return []
    try:
        token = refresh_google_token(client_id, secret, refresh)
        access = token.get("access_token") or ""
        if not access:
            return []
        cals = [
            c.strip()
            for c in str(settings.get("google_calendar_ids") or "primary").split(",")
            if c.strip()
        ]
        out = []
        for cal in cals or ["primary"]:
            out.extend(fetch_google_events(access, cal))
        return out
    except Exception:
        return []


def store_google_session(
    settings: Dict[str, Any],
    refresh_token: str,
    email: str = "",
) -> None:
    settings["google_calendar_refresh_token"] = protect_string(refresh_token or "")
    settings["google_calendar_email"] = str(email or "").strip()


def store_google_refresh(settings: Dict[str, Any], refresh_token: str) -> None:
    store_google_session(settings, refresh_token, str(settings.get("google_calendar_email") or ""))


def clear_google_session(settings: Dict[str, Any]) -> None:
    settings["google_calendar_refresh_token"] = ""
    settings["google_calendar_email"] = ""
