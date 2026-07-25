from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from app.config import BASE_DIR, ensure_data_dirs, ensure_encryption_key, get_settings


def main() -> None:
    # Garante que caminhos relativos (.env, data/) resolvem na pasta do projeto
    os.chdir(BASE_DIR)
    ensure_data_dirs()
    ensure_encryption_key()
    get_settings.cache_clear()
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
