"""Fernet-based encryption for API keys at rest.

The master key comes from ENCRYPTION_KEY; if absent it is generated once,
persisted to the key file with owner-only permissions, and printed once.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

_fernet: Optional[Fernet] = None


def _load_or_create_key() -> bytes:
    settings = get_settings()
    if settings.encryption_key:
        return settings.encryption_key.encode()

    key_file: Path = settings.encryption_key_file
    if key_file.exists():
        return key_file.read_bytes().strip()

    key = Fernet.generate_key()
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_bytes(key)
    try:
        os.chmod(key_file, 0o600)
    except OSError:
        pass  # best-effort on Windows
    print(
        "\n=== FreeLLM Gateway ===\n"
        f"Generated new ENCRYPTION_KEY (saved to {key_file}).\n"
        f"To keep keys portable across machines set:\n"
        f"ENCRYPTION_KEY={key.decode()}\n"
        "=======================\n",
        file=sys.stderr,
    )
    return key


def get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_or_create_key())
    return _fernet


def encrypt_key(plaintext: str) -> str:
    return get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_key(ciphertext: str) -> str:
    try:
        return get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Failed to decrypt API key — wrong ENCRYPTION_KEY?") from exc


def key_hint(plaintext: str) -> str:
    return plaintext[-4:] if len(plaintext) >= 4 else "****"
