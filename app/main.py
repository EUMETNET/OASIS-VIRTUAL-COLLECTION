"""OASIS Virtual Collection — OGC EDR compliant FastAPI application."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from app.config import get_settings
from app.logging_filters import EndpointFilter
from app.proxy import close_client
from app.proxy import init_client
from app.routers import collections
from app.routers import edr_queries
from app.routers import landing


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await init_client()
    yield
    await close_client()


settings = get_settings()

logging.getLogger("uvicorn.access").addFilter(
    EndpointFilter(settings.access_log_filter_paths)
)

app = FastAPI(
    title=settings.virtual_collection_title,
    description=(
        "An OGC EDR-compliant virtual collection API that proxies and translates "
        "requests to the Meteogate EDR API. Configure exposed parameters in "
        "`config/virtual_collections.json`."
    ),
    version="1.0.0",
    lifespan=lifespan,
    license_info={
        "name": "Apache 2.0",
        "url": "https://www.apache.org/licenses/LICENSE-2.0.html",
    },
    contact={
        "name": "OASIS",
        "url": settings.api_base_url,
    },
    swagger_ui_parameters={"tryItOutEnabled": True},
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(landing.router)
app.include_router(collections.router)
app.include_router(edr_queries.router)


@app.get("/health", tags=["Health"], include_in_schema=True)
async def health_check() -> dict[str, str]:
    """Liveness check — returns 200 OK when the service is running."""
    return {"status": "ok"}


@app.get("/api", include_in_schema=False)
async def openapi_json() -> dict:
    """Serve the OpenAPI JSON at /api (EDR spec §7.3 service-desc link)."""
    return get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
