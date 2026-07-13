"""Landing page and conformance endpoints (OGC EDR §7.2 / §7.4)."""

import os
from fastapi import APIRouter
from fastapi import Request
from edr_pydantic.capabilities import ConformanceModel
from edr_pydantic.capabilities import LandingPageModel
from edr_pydantic.link import Link

from app.config import get_settings

import logging

logger = logging.getLogger(__name__)
logger.setLevel(
    {"info": logging.INFO, "debug": logging.DEBUG}[
        os.getenv("LOG_LEVEL", "info").lower()
    ],
)

router = APIRouter(tags=["Capabilities"])


def _base_url(request: Request) -> str:
    settings = get_settings()
    return settings.api_base_url.rstrip("/")


@router.get(
    "/",
    response_model=LandingPageModel,
    summary="Landing page",
    description=(
        "The landing page provides links to the API definition, "
        "the conformance statements, and the available collections."
    ),
)
async def landing_page(request: Request) -> LandingPageModel:
    base = _base_url(request)
    settings = get_settings()
    return LandingPageModel(
        title=settings.virtual_collection_title,
        description=settings.virtual_collection_description,
        links=[
            Link(
                href=f"{base}/",
                rel="self",
                type="application/json",
                title="This document",
            ),
            Link(
                href=f"{base}/api",
                rel="service-desc",
                type="application/vnd.oai.openapi+json;version=3.0",
                title="OpenAPI definition",
            ),
            Link(
                href=f"{base}/docs",
                rel="service-doc",
                type="text/html",
                title="Interactive API documentation (Swagger UI)",
            ),
            Link(
                href=f"{base}/conformance",
                rel="conformance",
                type="application/json",
                title="OGC conformance classes implemented by this server",
            ),
            Link(
                href=f"{base}/collections",
                rel="data",
                type="application/json",
                title="Access the data",
            ),
        ],
    )


# OGC API — EDR conformance classes
_CONFORMANCE_CLASSES: list[str] = [
    "http://www.opengis.net/spec/ogcapi-edr-1/1.0/conf/core",
    "http://www.opengis.net/spec/ogcapi-edr-1/1.0/conf/collections",
    "http://www.opengis.net/spec/ogcapi-edr-1/1.0/conf/position",
    "http://www.opengis.net/spec/ogcapi-edr-1/1.0/conf/radius",
    "http://www.opengis.net/spec/ogcapi-edr-1/1.0/conf/area",

    "http://www.opengis.net/spec/ogcapi-edr-1/1.0/conf/corridor",
    "http://www.opengis.net/spec/ogcapi-edr-1/1.0/conf/locations",
    "http://www.opengis.net/spec/ogcapi-edr-1/1.0/conf/items",
    "http://www.opengis.net/spec/ogcapi-common-1/1.0/conf/core",
    "http://www.opengis.net/spec/ogcapi-common-2/0.0/conf/collections",
    "http://www.opengis.net/spec/ogcapi-common-1/1.0/conf/json",
    "http://www.opengis.net/spec/ogcapi-common-1/1.0/conf/oas30",
    "http://www.opengis.net/spec/ogcapi-edr-1/1.0/conf/oas30",
]


@router.get(
    "/conformance",
    response_model=ConformanceModel,
    summary="Conformance declarations",
    description=(
        "A list of all conformance classes specified in the OGC EDR standard "
        "that the server conforms to."
    ),
)
async def conformance() -> ConformanceModel:
    return ConformanceModel(conformsTo=_CONFORMANCE_CLASSES)
