"""Protect API keys at rest on Windows (DPAPI / Credential Manager blob).

settings.json stores `dpapi:<base64>` instead of plaintext when CryptProtectData
is available. Environment variables still win at use-time and are never written.
On POSIX the helper is a no-op (chmod 0600 remains the settings-file guard).
"""
from __future__ import annotations

import base64
import os
from typing import Any, Dict, Iterable, Mapping

DPAPI_PREFIX = "dpapi:"

SECRET_SETTING_KEYS = (
    "cursor_api_key",
    "gemini_api_key",
    "gemini_oauth_refresh_token",
    "google_oauth_client_secret",
    "anthropic_api_key",
    "azure_openai_api_key",
    "openai_compatible_api_key",
    "google_calendar_client_secret",
    "google_calendar_refresh_token",
    "telegram_bot_token",
    "telegram_api_hash",
)


def is_protected(value: str) -> bool:
    return isinstance(value, str) and value.startswith(DPAPI_PREFIX)


def protect_string(plain: str) -> str:
    """Encrypt for storage. Already-prefixed values are left unchanged."""
    if not plain or is_protected(plain):
        return plain or ""
    blob = _crypt_protect(plain)
    if blob is None:
        return plain
    return DPAPI_PREFIX + blob


def unprotect_string(stored: str) -> str:
    """Decrypt a stored value. Plaintext (no prefix) is returned as-is."""
    if not stored:
        return ""
    if not is_protected(stored):
        return stored
    raw = stored[len(DPAPI_PREFIX) :]
    plain = _crypt_unprotect(raw)
    return plain if plain is not None else ""


def protect_settings(settings: Mapping[str, Any], keys: Iterable[str] = SECRET_SETTING_KEYS) -> Dict[str, Any]:
    out = dict(settings)
    for key in keys:
        if key in out and isinstance(out[key], str) and out[key]:
            out[key] = protect_string(out[key])
    return out


def unprotect_settings(settings: Mapping[str, Any], keys: Iterable[str] = SECRET_SETTING_KEYS) -> Dict[str, Any]:
    out = dict(settings)
    for key in keys:
        if key in out and isinstance(out[key], str) and out[key]:
            out[key] = unprotect_string(out[key])
    return out


def _crypt_protect(plain: str) -> str | None:
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        data = plain.encode("utf-8")
        blob_in = DATA_BLOB(len(data), ctypes.create_string_buffer(data, len(data)))
        blob_out = DATA_BLOB()
        if not crypt32.CryptProtectData(
            ctypes.byref(blob_in),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(blob_out),
        ):
            return None
        try:
            buf = ctypes.string_at(blob_out.pbData, blob_out.cbData)
            return base64.b64encode(buf).decode("ascii")
        finally:
            kernel32.LocalFree(blob_out.pbData)
    except Exception:
        return None


def _crypt_unprotect(b64: str) -> str | None:
    if os.name != "nt":
        # Cannot decrypt a Windows blob on another OS.
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        raw = base64.b64decode(b64.encode("ascii"))
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        blob_in = DATA_BLOB(len(raw), ctypes.create_string_buffer(raw, len(raw)))
        blob_out = DATA_BLOB()
        if not crypt32.CryptUnprotectData(
            ctypes.byref(blob_in),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(blob_out),
        ):
            return None
        try:
            buf = ctypes.string_at(blob_out.pbData, blob_out.cbData)
            return buf.decode("utf-8")
        finally:
            kernel32.LocalFree(blob_out.pbData)
    except Exception:
        return None
