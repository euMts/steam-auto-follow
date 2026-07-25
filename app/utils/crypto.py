from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings
from app.utils.url_validation import derive_fernet_key


class CookieCryptoError(RuntimeError):
    pass


def _get_fernet() -> Fernet:
    settings = get_settings()
    key = (settings.cookie_encryption_key or "").strip()
    if not key:
        raise CookieCryptoError(
            "COOKIE_ENCRYPTION_KEY não configurada. Defina no arquivo .env."
        )
    return Fernet(derive_fernet_key(key))


def encrypt_value(plain: str) -> str:
    return _get_fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_value(token: str) -> str:
    try:
        return _get_fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise CookieCryptoError(
            "Falha ao descriptografar cookie. A chave pode ter mudado."
        ) from exc


def generate_encryption_key() -> str:
    return Fernet.generate_key().decode("utf-8")
