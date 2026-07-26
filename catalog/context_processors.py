from __future__ import annotations

from pathlib import Path
from typing import Any

from django.http import HttpRequest

_STATIC_DIR = Path(__file__).resolve().parent / "static" / "catalog"
_ASSETS = ("app.css", "app.js")


def asset_version(request: HttpRequest) -> dict[str, Any]:
    """Cache-busting token that changes whenever a static asset is edited.

    Derived from the newest asset mtime so the browser refetches after every
    change in development. Harmless in production, where hashed static URLs
    already handle busting.
    """
    try:
        newest = max((_STATIC_DIR / name).stat().st_mtime for name in _ASSETS)
        return {"asset_version": int(newest)}
    except OSError:
        return {"asset_version": 0}
