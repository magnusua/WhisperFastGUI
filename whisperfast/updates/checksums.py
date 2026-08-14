"""Parse and verify SHA-256 checksum files (GNU coreutils SHA256SUMS format)."""
from __future__ import annotations

import hashlib
import os
import re
from typing import Dict, Optional

# "hex  filename" or "hex *filename" (binary mode)
_GNU_LINE = re.compile(r"^([0-9a-fA-F]{64})(?:\s+\*|\s+)(.+?)\s*$")
# BSD: SHA256 (filename) = hex
_BSD_LINE = re.compile(
    r"^SHA256\s+\((.+)\)\s+=\s+([0-9a-fA-F]{64})\s*$",
    re.IGNORECASE,
)

HASH_CHUNK = 1024 * 1024


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(HASH_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def parse_sha256sums(text: str) -> Dict[str, str]:
    """Return {basename: lowercase hex} from a SHA256SUMS body."""
    mapping: Dict[str, str] = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        gnu = _GNU_LINE.match(line)
        if gnu:
            hex_digest, name = gnu.group(1), gnu.group(2).strip()
            mapping[os.path.basename(name.replace("\\", "/"))] = hex_digest.lower()
            continue
        bsd = _BSD_LINE.match(line)
        if bsd:
            name, hex_digest = bsd.group(1).strip(), bsd.group(2)
            mapping[os.path.basename(name.replace("\\", "/"))] = hex_digest.lower()
    return mapping


def expected_digest_for_filename(checksums: Dict[str, str], filename: str) -> Optional[str]:
    base = os.path.basename(filename.replace("\\", "/"))
    if base in checksums:
        return checksums[base]
    lower = {k.lower(): v for k, v in checksums.items()}
    return lower.get(base.lower())


def verify_file_sha256(path: str, expected_hex: str) -> bool:
    if not expected_hex:
        return False
    return sha256_file(path) == expected_hex.strip().lower()
