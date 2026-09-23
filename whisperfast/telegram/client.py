"""Minimal Telegram Bot API client for a local telegram-bot-api server.

JSON methods go through urllib. ``getFile`` in ``--local`` mode returns a
filesystem path; this client does not download the bytes itself.
"""
from __future__ import annotations

import json
import mimetypes
import os
import uuid
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional

JsonTransport = Callable[[str, Dict[str, Any], float], Dict[str, Any]]
UploadTransport = Callable[[str, Dict[str, Any], str, float], Dict[str, Any]]


class TelegramApiError(RuntimeError):
    """Bot API returned ok=false or the HTTP call failed."""


def unwrap_result(body: Any) -> Any:
    if not isinstance(body, dict) or not body.get("ok"):
        desc = ""
        if isinstance(body, dict):
            desc = str(body.get("description") or "")
        raise TelegramApiError(desc or "telegram api error")
    return body.get("result")


def urllib_json_transport(url: str, params: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    data = json.dumps(params).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return _read_json(req, timeout)


def urllib_upload_transport(
    url: str,
    fields: Dict[str, Any],
    file_path: str,
    timeout: float,
) -> Dict[str, Any]:
    body, content_type = _encode_multipart(fields, file_path)
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": content_type},
        method="POST",
    )
    return _read_json(req, timeout)


def _read_json(req: urllib.request.Request, timeout: float) -> Dict[str, Any]:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            raise TelegramApiError(raw.strip() or "telegram http error") from None
        if isinstance(parsed, dict):
            return parsed
        raise TelegramApiError("telegram http error") from None
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        raise TelegramApiError(str(reason or "connection failed")) from None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise TelegramApiError("telegram returned non-json") from None
    if not isinstance(parsed, dict):
        raise TelegramApiError("telegram returned non-json")
    return parsed


def _encode_multipart(fields: Dict[str, Any], file_path: str) -> tuple:
    boundary = "----ftw" + uuid.uuid4().hex
    filename = os.path.basename(file_path) or "file"
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    chunks = []
    for key, value in fields.items():
        if value is None:
            continue
        chunks.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")
        )
    chunks.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8")
    )
    with open(file_path, "rb") as handle:
        chunks.append(handle.read())
    chunks.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


class TelegramClient:
    def __init__(
        self,
        token: str,
        api_base: str = "http://127.0.0.1:8081",
        transport: Optional[JsonTransport] = None,
        upload: Optional[UploadTransport] = None,
    ):
        self.token = (token or "").strip()
        self.api_base = (api_base or "http://127.0.0.1:8081").rstrip("/")
        self._transport = transport or urllib_json_transport
        self._upload = upload or urllib_upload_transport

    def _url(self, method: str) -> str:
        return f"{self.api_base}/bot{self.token}/{method}"

    def call(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 60) -> Any:
        body = self._transport(self._url(method), dict(params or {}), timeout)
        return unwrap_result(body)

    def get_updates(self, offset: Optional[int] = None, timeout: int = 50) -> list:
        params: Dict[str, Any] = {
            "timeout": int(timeout),
            "allowed_updates": ["message"],
        }
        if offset is not None:
            params["offset"] = int(offset)
        result = self.call("getUpdates", params, timeout=float(timeout) + 15)
        return list(result or [])

    def get_file_path(self, file_id: str) -> str:
        result = self.call("getFile", {"file_id": file_id}, timeout=3600)
        if not isinstance(result, dict):
            raise TelegramApiError("getFile returned no file")
        path = str(result.get("file_path") or "")
        if not path:
            raise TelegramApiError("getFile returned no file_path")
        return path

    def send_message(self, chat_id: int, text: str, reply_to: Optional[int] = None) -> Any:
        params: Dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_to is not None:
            params["reply_to_message_id"] = int(reply_to)
        return self.call("sendMessage", params, timeout=60)

    def send_document(
        self,
        chat_id: int,
        path: str,
        caption: Optional[str] = None,
        reply_to: Optional[int] = None,
    ) -> Any:
        fields: Dict[str, Any] = {"chat_id": chat_id}
        if caption:
            fields["caption"] = caption
        if reply_to is not None:
            fields["reply_to_message_id"] = int(reply_to)
        body = self._upload(self._url("sendDocument"), fields, path, 3600)
        return unwrap_result(body)
