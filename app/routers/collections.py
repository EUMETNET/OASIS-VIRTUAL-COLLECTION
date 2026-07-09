"""
Collections metadata endpoints (OGC EDR §7.3).

Exposes a single "virtual" collection whose parameters are driven
entirely by config/virtual_collections.json.
"""

import os

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Request

import datetime

from edr_pydantic.collections import Collection
from edr_pydantic.collections import Collections
from edr_pydantic.collections import DataQueries
from edr_pydantic.data_queries import EDRQuery
from edr_pydantic.data_queries import EDRQueryLink
from edr_pydantic.extent import Extent
from edr_pydantic.extent import Spatial
from edr_pydantic.extent import Temporal
from edr_pydantic.link import Link
from edr_pydantic.observed_property import ObservedProperty
from edr_pydantic.parameter import Parameter
from edr_pydantic.parameter import Parameters
from edr_pydantic.unit import Symbol
from edr_pydantic.unit import Unit
from edr_pydantic.variables import Variables

from app.proxy import upstream_get

import logging

from app.config import ParameterConfig
from app.config import VirtualCollectionConfig
from app.config import get_settings
from app.config import get_virtual_collection_config

logger = logging.getLogger(__name__)

logger.setLevel(
    {"info": logging.INFO, "debug": logging.DEBUG}[
        os.getenv("LOG_LEVEL", "info").lower()
    ],
)

router = APIRouter(prefix="/collections", tags=["Collections"])


def _build_parameter(name: str, cfg: ParameterConfig) -> Parameter:
    symbol: Symbol | None = None
    if cfg.unit.symbol:
        symbol = Symbol(value=cfg.unit.symbol.value, type=cfg.unit.symbol.type)
    return Parameter(
        description=cfg.description or None,
        unit=Unit(label=cfg.unit.label, symbol=symbol),
        observedProperty=ObservedProperty(
            id=cfg.observed_property.id,
            label=cfg.observed_property.label,
        ),
    )


def _edr_query(base_url: str, collection_id: str, query_type: str) -> EDRQuery:
    return EDRQuery(
        link=EDRQueryLink(
            href=f"{base_url}/collections/{collection_id}/{query_type}",
            rel="data",
            variables=Variables(
                query_type=query_type,
                output_formats=["CoverageJSON"],
                default_output_format="CoverageJSON",
            ),
        )
    )


async def _build_collection(base_url: str, cfg: VirtualCollectionConfig) -> Collection:
    settings = get_settings()
    collection_id = settings.virtual_collection_id
    logger.debug(
        "Building collection '%s' with %d parameter(s)", collection_id, len(cfg)
    )

    parameter_names = Parameters(
        {name: _build_parameter(name, param_cfg) for name, param_cfg in cfg.items()}
    )

    data_queries = DataQueries(
        position=_edr_query(base_url, collection_id, "position"),
        radius=_edr_query(base_url, collection_id, "radius"),
        area=_edr_query(base_url, collection_id, "area"),
        cube=_edr_query(base_url, collection_id, "cube"),
        locations=_edr_query(base_url, collection_id, "locations"),
        items=_edr_query(base_url, collection_id, "items"),
    )

    upstream_extens = await upstream_get("/collections/observations")
    upstream_extens = upstream_extens.json()
    upstream_extens = {
        k: upstream_extens["extent"].get(k)
        for k in upstream_extens["extent"]
        if k in ["spatial", "temporal"]
    }
    print(upstream_extens["temporal"])

    temporal = Temporal(
        interval=[
            [
                datetime.datetime.fromisoformat(i)
                for i in upstream_extens["temporal"]["interval"][0]
            ]
        ],
        values=upstream_extens["temporal"]["values"],
        trs="Gregorian",
    )

    return Collection(
        id=collection_id,
        title=settings.virtual_collection_title,
        description=settings.virtual_collection_description,
        links=[
            Link(
                href=f"{base_url}/collections/{collection_id}",
                rel="self",
                type="application/json",
                title="This collection",
            ),
            Link(
                href=f"{base_url}/collections/{collection_id}/position",
                rel="data",
                type="application/prs.coverage+json",
                title="Position query",
            ),
        ],
        extent=Extent(
            spatial=Spatial(**upstream_extens["spatial"]),
            temporal=temporal,
        ),
        data_queries=data_queries,
        parameter_names=parameter_names,
        crs=["http://www.opengis.net/def/crs/OGC/1.3/CRS84"],
        output_formats=["CoverageJSON", "GeoJSON"],
    )


@router.get(
    "",
    response_model=Collections,
    summary="List available collections",
    description="Returns all available data collections, including the virtual collection.",
)
async def list_collections(request: Request) -> Collections:
    logger.debug("Listing collections")
    settings = get_settings()
    base_url = settings.api_base_url.rstrip("/")
    cfg = get_virtual_collection_config()
    collection = await _build_collection(base_url, cfg)
    return Collections(
        links=[
            Link(
                href=f"{base_url}/collections",
                rel="self",
                type="application/json",
                title="This document",
            )
        ],
        collections=[collection],
    )


@router.get(
    "/{collection_id}",
    response_model=Collection,
    summary="Describe a collection",
    description="Returns metadata for the specified collection.",
)
async def get_collection(collection_id: str, request: Request) -> Collection:
    settings = get_settings()
    if collection_id != settings.virtual_collection_id:
        logger.debug("Collection '%s' not found", collection_id)
        raise HTTPException(
            status_code=404,
            detail=(
                f"Collection '{collection_id}' not found. "
                f"Available collection: '{settings.virtual_collection_id}'"
            ),
        )
    base_url = settings.api_base_url.rstrip("/")
    cfg = get_virtual_collection_config()
    return await _build_collection(base_url, cfg)
