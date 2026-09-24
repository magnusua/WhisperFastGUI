"""Meeting calendar: ICS file/URL, Outlook COM, Google Calendar OAuth (optional)."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from whisperfast.core.google_oauth import (
    CALENDAR_SCOPE,
    CLIENT_ID_ENV,
    GOOGLE_CALENDAR_API_URL,
    GOOGLE_CLOUD_CREDENTIALS_URL,
    GOOGLE_SCOPE,
    GoogleLoopbackAuth,
    exchange_google_code,
    extract_oauth_code_from_url,
    fetch_google_email,
    google_auth_url,
    make_pkce,
    refresh_google_token,
    resolve_google_client_id,
)
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


GOOGLE_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/{cal}/events"


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
_async_refresh_lock = threading.Lock()
_async_refresh_in_flight = False


def collect_events(settings: Dict[str, Any], force: bool = False) -> List[dict]:
    """Outlook COM + Google/ICS HTTP calls happen here — blocking, up to ~20-40s
    on a slow/unreachable network. Callers on the Tk main thread (e.g. a 1s
    poll loop) should use collect_events_async() instead."""
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


def get_cached_events() -> List[dict]:
    """Last computed event list, without ever blocking on network/COM I/O."""
    with _cache_lock:
        return list(_cache["events"])


def collect_events_async(settings: Dict[str, Any]) -> List[dict]:
    """Non-blocking counterpart to collect_events() for the Tk main thread.

    Returns the current cached snapshot immediately. If that snapshot is
    stale (past collect_events()'s own 60s TTL), kicks off a single
    background refresh (subsequent calls made while it's running just return
    the still-cached snapshot) instead of blocking the caller on Outlook COM
    or Google/ICS HTTP requests.
    """
    global _async_refresh_in_flight
    now = time.time()
    with _cache_lock:
        cached = list(_cache["events"])
        is_fresh = bool(_cache["events"]) and now - float(_cache["ts"] or 0) < 60
    if is_fresh:
        return cached

    with _async_refresh_lock:
        if _async_refresh_in_flight:
            return cached
        _async_refresh_in_flight = True

    def _worker():
        global _async_refresh_in_flight
        try:
            collect_events(settings)
        except Exception:
            pass
        finally:
            with _async_refresh_lock:
                _async_refresh_in_flight = False

    threading.Thread(target=_worker, daemon=True, name="ftw-calendar-refresh").start()
    return cached


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
    settings["google_calendar_refresh_token"] = protect_string(
        refresh_token or "", label="google_calendar_refresh_token"
    )
    settings["google_calendar_email"] = str(email or "").strip()


def clear_google_session(settings: Dict[str, Any]) -> None:
    settings["google_calendar_refresh_token"] = ""
    settings["google_calendar_email"] = ""
