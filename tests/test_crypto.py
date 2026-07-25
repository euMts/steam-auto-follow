from __future__ import annotations

import os

import pytest

# Chave de teste antes de importar módulos que leem settings
os.environ.setdefault(
    "COOKIE_ENCRYPTION_KEY",
    "test-encryption-key-for-unit-tests-only",
)

from app.utils.crypto import CookieCryptoError, decrypt_value, encrypt_value


def test_encrypt_decrypt_roundtrip():
    token = encrypt_value("valor-secreto")
    assert token != "valor-secreto"
    assert decrypt_value(token) == "valor-secreto"


def test_decrypt_invalid_token():
    with pytest.raises(CookieCryptoError):
        decrypt_value("token-invalido")
