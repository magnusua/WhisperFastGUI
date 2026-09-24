"""Protect API keys at rest.

Windows stores `dpapi:<base64>` (CryptProtectData). macOS stores the secret in
the login Keychain and writes `keychain:<setting>` into settings.json. Linux
keeps the plaintext and relies on chmod 0600. Environment variables still win
at use-time and are never written.
"""
from __future__ import annotations

import base64
import os
import re
import sys
from typing import Any, Dict, Iterable, Mapping

DPAPI_PREFIX = "dpapi:"
KEYCHAIN_PREFIX = "keychain:"
_KEYCHAIN_SERVICE = "FTW"
_LABEL_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
# Accounts whose Keychain read failed this process. A later save must not
# treat the resulting empty string as "user cleared the secret".
_unreadable_accounts: set[str] = set()

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
    return isinstance(value, str) and (
        value.startswith(DPAPI_PREFIX) or value.startswith(KEYCHAIN_PREFIX)
    )


def protect_string(plain: str, *, label: str = "") -> str:
    """Encrypt for storage. Already-prefixed values are left unchanged."""
    if not plain or is_protected(plain):
        return plain or ""
    if sys.platform == "darwin" and _LABEL_RE.fullmatch(label or ""):
        if _keychain_set(label, plain):
            _unreadable_accounts.discard(label)
            return KEYCHAIN_PREFIX + label
        return plain
    blob = _crypt_protect(plain)
    if blob is None:
        return plain
    return DPAPI_PREFIX + blob


def unprotect_string(stored: str) -> str:
    """Decrypt a stored value. Plaintext (no prefix) is returned as-is."""
    if not stored:
        return ""
    if stored.startswith(KEYCHAIN_PREFIX):
        account = stored[len(KEYCHAIN_PREFIX) :]
        plain = _keychain_get(account)
        if plain is None:
            _unreadable_accounts.add(account)
            return ""
        _unreadable_accounts.discard(account)
        return plain
    if not stored.startswith(DPAPI_PREFIX):
        return stored
    raw = stored[len(DPAPI_PREFIX) :]
    plain = _crypt_unprotect(raw)
    return plain if plain is not None else ""


def protect_settings(settings: Mapping[str, Any], keys: Iterable[str] = SECRET_SETTING_KEYS) -> Dict[str, Any]:
    out = dict(settings)
    for key in keys:
        if key not in out or not isinstance(out[key], str):
            continue
        if out[key]:
            out[key] = protect_string(out[key], label=key)
            continue
        if key in _unreadable_accounts:
            out[key] = KEYCHAIN_PREFIX + key
            continue
        _keychain_delete(key)
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


_ERR_SEC_SUCCESS = 0
_ERR_SEC_DUPLICATE_ITEM = -25299
_ERR_SEC_ITEM_NOT_FOUND = -25300


def _load_security():
    if sys.platform != "darwin":
        return None, None
    try:
        import ctypes
        import ctypes.util

        security_path = ctypes.util.find_library("Security")
        core_path = ctypes.util.find_library("CoreFoundation")
        if not security_path or not core_path:
            return None, None
        security = ctypes.cdll.LoadLibrary(security_path)
        core = ctypes.cdll.LoadLibrary(core_path)
        c_void_p = ctypes.c_void_p
        c_uint32 = ctypes.c_uint32
        c_char_p = ctypes.c_char_p
        c_int32 = ctypes.c_int32
        security.SecKeychainAddGenericPassword.argtypes = [
            c_void_p, c_uint32, c_char_p, c_uint32, c_char_p, c_uint32, c_void_p, c_void_p,
        ]
        security.SecKeychainAddGenericPassword.restype = c_int32
        security.SecKeychainFindGenericPassword.argtypes = [
            c_void_p, c_uint32, c_char_p, c_uint32, c_char_p,
            ctypes.POINTER(c_uint32), ctypes.POINTER(c_void_p), ctypes.POINTER(c_void_p),
        ]
        security.SecKeychainFindGenericPassword.restype = c_int32
        security.SecKeychainItemModifyAttributesAndData.argtypes = [
            c_void_p, c_void_p, c_uint32, c_void_p,
        ]
        security.SecKeychainItemModifyAttributesAndData.restype = c_int32
        security.SecKeychainItemDelete.argtypes = [c_void_p]
        security.SecKeychainItemDelete.restype = c_int32
        security.SecKeychainItemFreeContent.argtypes = [c_void_p, c_void_p]
        security.SecKeychainItemFreeContent.restype = c_int32
        core.CFRelease.argtypes = [c_void_p]
        core.CFRelease.restype = None
        return security, core
    except Exception:
        return None, None


def _keychain_names(account: str):
    if not _LABEL_RE.fullmatch(account or ""):
        return None
    return _KEYCHAIN_SERVICE.encode("utf-8"), account.encode("utf-8")


def _keychain_set(account: str, secret: str) -> bool:
    security, core = _load_security()
    names = _keychain_names(account)
    if security is None or names is None:
        return False
    service_b, account_b = names
    try:
        import ctypes

        data = ctypes.create_string_buffer(secret.encode("utf-8"))
        status = security.SecKeychainAddGenericPassword(
            None,
            len(service_b),
            service_b,
            len(account_b),
            account_b,
            len(secret.encode("utf-8")),
            ctypes.cast(data, ctypes.c_void_p),
            None,
        )
        if status == _ERR_SEC_SUCCESS:
            return True
        if status != _ERR_SEC_DUPLICATE_ITEM:
            return False
        return _keychain_update(security, core, service_b, account_b, secret)
    except Exception:
        return False


def _keychain_update(security, core, service_b: bytes, account_b: bytes, secret: str) -> bool:
    import ctypes

    item = ctypes.c_void_p()
    status = security.SecKeychainFindGenericPassword(
        None,
        len(service_b),
        service_b,
        len(account_b),
        account_b,
        None,
        None,
        ctypes.byref(item),
    )
    if status != _ERR_SEC_SUCCESS or not item.value:
        return False
    try:
        data = ctypes.create_string_buffer(secret.encode("utf-8"))
        status = security.SecKeychainItemModifyAttributesAndData(
            item,
            None,
            len(secret.encode("utf-8")),
            ctypes.cast(data, ctypes.c_void_p),
        )
        return status == _ERR_SEC_SUCCESS
    finally:
        core.CFRelease(item)


def _keychain_get(account: str) -> str | None:
    """Return the secret, "" if the item is missing, None if the lookup failed."""
    if sys.platform != "darwin":
        return None
    security, core = _load_security()
    names = _keychain_names(account)
    if security is None or names is None:
        return None
    service_b, account_b = names
    try:
        import ctypes

        length = ctypes.c_uint32()
        data = ctypes.c_void_p()
        item = ctypes.c_void_p()
        status = security.SecKeychainFindGenericPassword(
            None,
            len(service_b),
            service_b,
            len(account_b),
            account_b,
            ctypes.byref(length),
            ctypes.byref(data),
            ctypes.byref(item),
        )
        if status == _ERR_SEC_ITEM_NOT_FOUND:
            if item.value:
                core.CFRelease(item)
            return ""
        if status != _ERR_SEC_SUCCESS or not data.value:
            if item.value:
                core.CFRelease(item)
            return None
        try:
            return ctypes.string_at(data.value, length.value).decode("utf-8")
        finally:
            security.SecKeychainItemFreeContent(None, data)
            if item.value:
                core.CFRelease(item)
    except Exception:
        return None


def _keychain_delete(account: str) -> None:
    if sys.platform != "darwin":
        return
    security, core = _load_security()
    names = _keychain_names(account)
    if security is None or names is None:
        return
    service_b, account_b = names
    try:
        import ctypes

        item = ctypes.c_void_p()
        status = security.SecKeychainFindGenericPassword(
            None,
            len(service_b),
            service_b,
            len(account_b),
            account_b,
            None,
            None,
            ctypes.byref(item),
        )
        if status != _ERR_SEC_SUCCESS or not item.value:
            return
        try:
            security.SecKeychainItemDelete(item)
        finally:
            core.CFRelease(item)
    except Exception:
        return
