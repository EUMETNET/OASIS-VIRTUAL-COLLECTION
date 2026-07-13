"""Async HTTP client for proxying requests to the upstream EDR API."""

import os
from typing import Any

import httpx
from fastapi import HTTPException

from app.config import Settings
from app.config import get_settings

import logging

logger = logging.getLogger(__name__)


logger.setLevel(
    {"info": logging.INFO, "debug": logging.DEBUG}[
        os.getenv("LOG_LEVEL", "info").lower()
    ],
)

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
    logger.debug(
        "HTTP client initialised with base_url=%s", settings.upstream_edr_base_url
    )


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
        logger.debug("HTTP client closed")


async def upstream_get(path: str, params: dict[str, Any] | None = None) -> Any:
    """Perform a GET against the upstream EDR API and return the parsed JSON body."""
    client = get_client()
    logger.debug("Upstream GET path=%s params=%s", path, params)
    try:
        response = await client.get(path, params=params)
        response.raise_for_status()
        logger.debug("Upstream GET path=%s status=%s", path, response.status_code)
        return response.json()
    except httpx.HTTPStatusError as exc:
        logger.debug(
            "Upstream HTTP error for path=%s: status=%s body=%s",
            path,
            exc.response.status_code,
            exc.response.text,
        )
        raise ValueError(
            f"Upstream API error: {exc.response.text} status_code={exc.response.status_code}",
        ) from exc
    except httpx.RequestError as exc:
        logger.warning("Could not reach upstream EDR API for path=%s: %s", path, exc)
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach upstream EDR API: {exc}",
        ) from exc
