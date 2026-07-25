from __future__ import annotations

import base64
import hashlib
import re
from urllib.parse import urlparse

from app.config import ALLOWED_STEAM_DOMAINS

BLOCKED_SCHEMES = {"file", "javascript", "data", "vbscript", "about", "blob"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"}


class InvalidSteamUrlError(ValueError):
    pass


def looks_like_steam_url(url: str) -> bool:
    """Descarta rapidamente links que não mencionam steam."""
    return "steam" in (url or "").lower()


def filter_steam_urls(urls: list[str]) -> list[str]:
    """Mantém só linhas que parecem Steam; ignora o resto sem erro."""
    return [u.strip() for u in urls if u.strip() and looks_like_steam_url(u)]


def validate_steam_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        raise InvalidSteamUrlError("URL vazia")

    if not looks_like_steam_url(raw):
        raise InvalidSteamUrlError("URL não parece ser da Steam")

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()

    if scheme in BLOCKED_SCHEMES:
        raise InvalidSteamUrlError(f"Protocolo não permitido: {scheme}")

    if scheme != "https":
        raise InvalidSteamUrlError("Apenas URLs HTTPS são aceitas")

    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise InvalidSteamUrlError("Host inválido")

    if host in LOCAL_HOSTS or host.endswith(".local") or host.endswith(".internal"):
        raise InvalidSteamUrlError("Endereços locais não são permitidos")

    if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host):
        raise InvalidSteamUrlError("IPs literais não são permitidos")

    if not any(host == domain or host.endswith(f".{domain}") for domain in ALLOWED_STEAM_DOMAINS):
        raise InvalidSteamUrlError(
            "URL deve pertencer a um domínio permitido da Steam "
            f"({', '.join(ALLOWED_STEAM_DOMAINS)})"
        )

    return raw


def detect_action_type(url: str) -> str:
    """Infere o tipo de ação com base no caminho da URL Steam."""
    parsed = urlparse(url.strip())
    path = (parsed.path or "").lower()
    host = (parsed.hostname or "").lower()

    if "/app/" in path:
        return "wishlist_and_follow_app"
    if "/curator/" in path:
        return "follow_curator"
    if "/publisher/" in path or "/developer/" in path:
        return "follow_publisher"
    if "/groups/" in path or (host.endswith("steamcommunity.com") and "/gid/" in path):
        return "follow_group"

    raise InvalidSteamUrlError(
        "Não foi possível detectar a ação pela URL. "
        "Use /app/, /curator/, /publisher/, /developer/ ou /groups/."
    )


def mask_secret(value: str | None, keep: int = 3) -> str | None:
    if not value:
        return None
    if len(value) <= keep * 2:
        return "***"
    return f"{value[:keep]}...{value[-keep:]}"


def sanitize_log_message(message: str) -> str:
    """Remove possíveis valores sensíveis de mensagens de log."""
    patterns = [
        (r"(steamLoginSecure[=:\s]+)([^\s&\"']+)", r"\1[REDACTED]"),
        (r"(sessionid[=:\s]+)([^\s&\"']+)", r"\1[REDACTED]"),
        (r"(Bearer\s+)([^\s]+)", r"\1[REDACTED]"),
    ]
    result = message
    for pattern, repl in patterns:
        result = re.sub(pattern, repl, result, flags=re.IGNORECASE)
    return result


def derive_fernet_key(raw_key: str) -> bytes:
    """Deriva uma chave Fernet válida a partir da variável de ambiente."""
    digest = hashlib.sha256(raw_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)
