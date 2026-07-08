"""Async HTTP client for proxying requests to the upstream EDR API."""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import httpx
from fastapi import HTTPException

from app.config import Settings
from app.config import get_settings

# A single shared async client, created at startup and closed at shutdown
_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError("HTTP client not initialised. Call init_client() first.")
    return _client


async def init_client(settings: Settings | None = None) -> None:
    global _client
    if settings is None:
        settings = get_settings()
    headers: dict[str, str] = {"Accept": "application/json"}
    if settings.upstream_edr_api_key:
        headers["X-API-Key"] = settings.upstream_edr_api_key
    _client = httpx.AsyncClient(
        base_url=settings.upstream_edr_base_url,
        headers=headers,
        timeout=30.0,
        follow_redirects=True,
    )


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def upstream_get(path: str, params: dict[str, Any] | None = None) -> Any:
    """Perform a GET against the upstream EDR API and return the parsed JSON body."""
    client = get_client()
    try:
        print(path, params)
        response = await client.get(path, params=params)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"Upstream API error: {exc.response.text}",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach upstream EDR API: {exc}",
        ) from exc


async def upstream_get_raw(path: str, params: dict[str, Any] | None = None) -> bytes:
    """Return the raw bytes from an upstream response (e.g. for CoverageJSON pass-through)."""
    client = get_client()
    try:
        print(path, params)
        response = await client.get(path, params=params)
        response.raise_for_status()
        return response.content
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"Upstream API error: {exc.response.text}",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach upstream EDR API: {exc}",
        ) from exc
