"""
EDR data query endpoints (OGC EDR §7.5–§7.12).

Every query type proxies to the upstream Meteogate EDR API using the
parameter mapping defined in config/virtual_collections.json.

Supported query types:
  position, radius, area, cube, trajectory, corridor, locations, items
"""

import os
import json
import logging
from collections import defaultdict
from typing import Any

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi import Response
from fastapi.responses import JSONResponse

from app.config import ParameterConfig
from app.config import VirtualCollectionConfig
from app.config import get_settings
from app.config import get_virtual_collection_config
from app.proxy import upstream_get

logger = logging.getLogger(__name__)

logger.setLevel(
    {"info": logging.INFO, "debug": logging.DEBUG}[
        os.getenv("LOG_LEVEL", "info").lower()
    ],
)
router = APIRouter(
    prefix="/collections/{collection_id}",
    tags=["EDR Queries"],
)

# Media type used for CoverageJSON responses
COVJSON_MEDIA_TYPE = "application/prs.coverage+json"
GEOJSON_MEDIA_TYPE = "application/geo+json"
JSON_MEDIA_TYPE = "application/json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_collection(collection_id: str) -> None:
    settings = get_settings()
    if collection_id != settings.virtual_collection_id:
        logger.debug("Collection '%s' not found", collection_id)
        raise HTTPException(
            status_code=404,
            detail=f"Collection '{collection_id}' not found.",
        )


# Group key: (upstream_collection, sorted custom_dimensions) — parameters that differ
# in their custom dimensions must be sent as separate upstream requests even when they
# share the same upstream collection.
_GroupKey = tuple[str, tuple[tuple[str, str], ...]]


def _resolve_parameters(
    parameter_name: str | None,
    cfg: VirtualCollectionConfig,
) -> dict[_GroupKey, list[tuple[str, ParameterConfig]]]:
    """
    Map the requested virtual parameter-names to upstream requests.

    Returns a dict keyed by (upstream_collection, custom_dimensions) so that
    parameters are batched together only when they share both the same collection
    and the same custom dimension values.
    """
    if parameter_name:
        requested = [p.strip() for p in parameter_name.split(",") if p.strip()]
    else:
        requested = list(cfg.keys())

    unknown = [p for p in requested if p not in cfg]
    if unknown:
        logger.warning(
            "Unknown parameter-name(s) requested: %s. Available: %s",
            ", ".join(unknown),
            ", ".join(cfg.keys()),
        )
        raise HTTPException(
            status_code=400,
            detail=f"Unknown parameter-name(s): {', '.join(unknown)}. "
            f"Available: {', '.join(cfg.keys())}",
        )

    grouped: dict[_GroupKey, list[tuple[str, ParameterConfig]]] = defaultdict(list)
    for name in requested:
        param_cfg = cfg[name]
        dims_key: tuple[tuple[str, str], ...] = tuple(
            sorted(param_cfg.custom_dimensions.items())
        )
        grouped[(param_cfg.upstream_collection, dims_key)].append((name, param_cfg))
    logger.debug(
        "Resolved %d virtual parameter(s) into %d upstream group(s)",
        len(requested),
        len(grouped),
    )
    return grouped


async def _proxy_query(
    query_type: str,
    upstream_collection: str,
    upstream_params: list[tuple[str, ParameterConfig]],
    extra_params: dict[str, Any],
) -> Any:
    """Issue one upstream EDR query and return the parsed JSON."""
    # All params in this group share the same custom_dimensions (guaranteed by grouping).
    custom_dims = upstream_params[0][1].custom_dimensions if upstream_params else {}
    params: dict[str, Any] = {**extra_params, **custom_dims}
    # Remove None values
    params = {k: v for k, v in params.items() if v is not None}
    path = f"/collections/{upstream_collection}/{query_type}"
    logger.debug("Proxying %s → %s params=%s", query_type, path, params)
    return await upstream_get(path, params=params)


def _build_covjson_parameter(name: str, cfg: ParameterConfig) -> dict[str, Any]:
    """
    Build a canonical CoverageJSON Parameter object from a ParameterConfig.

    This is the authoritative, simplified form we expose — derived entirely
    from virtual_collections.json rather than whatever the upstream returns.
    """
    param: dict[str, Any] = {
        "type": "Parameter",
        "observedProperty": {
            "id": cfg.observed_property.id,
            "label": cfg.observed_property.label,
        },
        "unit": {
            "label": cfg.unit.label,
        },
        "dataType": cfg.data_type,
    }
    if cfg.description:
        param["description"] = cfg.description
    if cfg.unit.symbol:
        param["unit"]["symbol"] = {
            "value": cfg.unit.symbol.value,
            "type": cfg.unit.symbol.type,
        }
    return param


def _remap_coverage_parameter_names(
    coverage: dict[str, Any],
    upstream_params: list[tuple[str, ParameterConfig]],
) -> dict[str, Any]:
    """
    Rewrite upstream parameter keys to our virtual parameter names.

    Handles CoverageJSON Coverage, CoverageCollection, and OGC EDR GeoJSON
    FeatureCollection responses (which carry a top-level ``parameters`` dict).

    The upstream uses compound keys such as ``air_temperature:2.0:point:PT0S``
    rather than the bare standard name.  Each upstream parameter object carries
    a ``metocean:standard_name`` extension field that we use to match it to the
    correct virtual parameter from our config.  Any parameter that cannot be
    matched is passed through with its key unchanged.
    """
    # standard_name value -> virtual parameter name (from config)
    standard_name_to_virtual: dict[str, str] = {
        p_cfg.custom_dimensions["standard_name"]: vname
        for vname, p_cfg in upstream_params
        if "standard_name" in p_cfg.custom_dimensions
    }

    def make_key_map(params_dict: dict[str, Any]) -> dict[str, str]:
        """Derive upstream-key -> virtual-name map from the parameter objects."""
        key_map: dict[str, str] = {}
        for upstream_key, param_obj in params_dict.items():
            if not isinstance(param_obj, dict):
                continue
            sn = param_obj.get("metocean:standard_name")
            if sn and sn in standard_name_to_virtual:
                key_map[upstream_key] = standard_name_to_virtual[sn]
        return key_map

    def remap_dict(d: dict[str, Any], key_map: dict[str, str]) -> dict[str, Any]:
        return {key_map.get(k, k): v for k, v in d.items()}

    def remap_single_coverage(cov: dict[str, Any]) -> dict[str, Any]:
        """Remap parameters and ranges inside one Coverage object."""
        key_map = make_key_map(cov.get("parameters", {}))
        if key_map:
            logger.debug(
                "Remapping %d parameter key(s): %s", len(key_map), list(key_map.keys())
            )
        if "parameters" in cov:
            cov["parameters"] = remap_dict(cov["parameters"], key_map)
        if "ranges" in cov:
            cov["ranges"] = remap_dict(cov["ranges"], key_map)
        return cov

    cov_type = coverage.get("type", "")
    if cov_type == "CoverageCollection":
        if "parameters" in coverage:
            top_key_map = make_key_map(coverage["parameters"])
            coverage["parameters"] = remap_dict(coverage["parameters"], top_key_map)
        coverage["coverages"] = [
            remap_single_coverage(c) for c in coverage.get("coverages", [])
        ]
    elif cov_type == "Coverage":
        coverage = remap_single_coverage(coverage)
    elif cov_type == "FeatureCollection":
        key_map: dict[str, str] = {}
        if "parameters" in coverage:
            key_map = make_key_map(coverage["parameters"])
            coverage["parameters"] = remap_dict(coverage["parameters"], key_map)
        if key_map:
            for feature in coverage.get("features", []):
                props = feature.get("properties") or {}
                if isinstance(props.get("parameter-name"), list):
                    props["parameter-name"] = [
                        key_map.get(k, k) for k in props["parameter-name"]
                    ]

    return coverage


def _rewrite_parameter_metadata(
    coverage: dict[str, Any],
    upstream_params: list[tuple[str, ParameterConfig]],
) -> dict[str, Any]:
    """
    Replace upstream parameter metadata with the canonical form from our config.

    This is called *after* _remap_coverage_parameter_names, so the keys in
    ``coverage["parameters"]`` are already the virtual names.  For each virtual
    name present in the response we substitute the entire parameter object with
    the one built from ParameterConfig; any extra parameters returned by the
    upstream that we don't own are left untouched.

    Handles CoverageJSON Coverage, CoverageCollection, and OGC EDR GeoJSON
    FeatureCollection responses (which carry a top-level ``parameters`` dict).
    """
    canonical: dict[str, dict[str, Any]] = {
        vname: _build_covjson_parameter(vname, cfg) for vname, cfg in upstream_params
    }

    def rewrite_params(params_dict: dict[str, Any]) -> dict[str, Any]:
        return {k: canonical.get(k, v) for k, v in params_dict.items()}

    cov_type = coverage.get("type", "")
    if cov_type == "CoverageCollection":
        if "parameters" in coverage:
            coverage["parameters"] = rewrite_params(coverage["parameters"])
        coverage["coverages"] = [
            {**c, "parameters": rewrite_params(c["parameters"])}
            if "parameters" in c
            else c
            for c in coverage.get("coverages", [])
        ]
    elif cov_type == "Coverage":
        if "parameters" in coverage:
            coverage["parameters"] = rewrite_params(coverage["parameters"])
    elif cov_type == "FeatureCollection":
        if "parameters" in coverage:
            coverage["parameters"] = rewrite_params(coverage["parameters"])

    return coverage


def _rewrite_upstream_urls(
    data: dict[str, Any],
    upstream_collections: set[str],
) -> dict[str, Any]:
    """
    Replace upstream API URLs in all link objects with our own API URLs.

    Walks the top-level ``links`` array and each feature's ``links`` array in a
    GeoJSON FeatureCollection, rewriting every ``href`` that points at the
    upstream EDR API so that clients receive URLs rooted at this API instead.

    For each upstream collection name the substitution is:
      {upstream_base}/collections/{upstream_collection}/...
        → {api_base}/collections/{virtual_collection_id}/...

    Any remaining upstream base-URL prefixes (e.g. links to other upstream
    resources) are also rewritten to the local base URL.
    """
    settings = get_settings()
    upstream_base = settings.upstream_edr_base_url.rstrip("/")
    api_base = settings.api_base_url.rstrip("/")
    virtual_id = settings.virtual_collection_id

    # Most-specific replacements first so collection paths are rewritten before
    # the bare host fallback has a chance to match.
    replacements: list[tuple[str, str]] = [
        (
            f"{upstream_base}/collections/{col}",
            f"{api_base}/collections/{virtual_id}",
        )
        for col in sorted(upstream_collections)
    ]
    replacements.append((upstream_base, api_base))

    def rewrite_href(href: str) -> str:
        for old, new in replacements:
            if href.startswith(old):
                return new + href[len(old) :]
        return href

    def rewrite_links(links: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {**link, "href": rewrite_href(link["href"])} if "href" in link else link
            for link in links
        ]

    if "links" in data:
        rewritten = rewrite_links(data["links"])
        n_rewritten = sum(1 for old, new in zip(data["links"], rewritten) if old != new)
        if n_rewritten:
            logger.debug("Rewrote %d top-level link(s) to local API URLs", n_rewritten)
        data["links"] = rewritten

    if "features" in data:
        for feature in data["features"]:
            if "links" in feature:
                feature["links"] = rewrite_links(feature["links"])

    return data


async def _run_query(
    query_type: str,
    collection_id: str,
    parameter_name: str | None,
    extra_params: dict[str, Any],
) -> Response:
    logger.debug(
        "Query type='%s' collection='%s' parameter_name=%s",
        query_type,
        collection_id,
        parameter_name or "<all>",
    )
    _check_collection(collection_id)
    cfg = get_virtual_collection_config()
    grouped = _resolve_parameters(parameter_name, cfg)

    results: list[dict[str, Any]] = []
    upstream_params_per_group: list[list[tuple[str, ParameterConfig]]] = []

    for (upstream_collection, _dims_key), upstream_params in grouped.items():
        response = await _proxy_query(
            query_type, upstream_collection, upstream_params, extra_params
        )
        logger.debug(
            "Upstream response type='%s'",
            response.get("type")
            if isinstance(response, dict)
            else type(response).__name__,
        )
        results.append(response.json())
        upstream_params_per_group.append(upstream_params)

    if len(results) == 0:
        raise HTTPException(status_code=404, detail="No data found.")

    # Remap keys then rewrite parameter metadata to our canonical form
    remapped = [
        _rewrite_parameter_metadata(_remap_coverage_parameter_names(r, p), p)
        for r, p in zip(results, upstream_params_per_group)
    ]

    if len(remapped) == 1:
        merged = remapped[0]
    else:
        merged = _merge_coverages(remapped)

    # For the locations listing rewrite upstream URLs so clients only see our API
    if query_type == "locations":
        upstream_collections = {col for (col, _) in grouped}
        merged = _rewrite_upstream_urls(merged, upstream_collections)

    # Detect response media type from the result
    media_type = _detect_media_type(merged)
    return Response(
        content=json.dumps(merged),
        media_type=media_type,
    )


def _merge_coverages(coverages: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge multiple CoverageJSON responses into one CoverageCollection or Coverage."""
    logger.debug("Merging %d coverage response(s)", len(coverages))
    base = coverages[0]
    cov_type = base.get("type", "")

    if cov_type == "CoverageCollection":
        merged_parameters: dict[str, Any] = {}
        merged_coverages: list[Any] = []
        for cov in coverages:
            merged_parameters.update(cov.get("parameters", {}))
            merged_coverages.extend(cov.get("coverages", []))
        base["parameters"] = merged_parameters
        base["coverages"] = merged_coverages
        return base

    elif cov_type == "Coverage":
        merged_parameters = {}
        merged_ranges: dict[str, Any] = {}
        for cov in coverages:
            merged_parameters.update(cov.get("parameters", {}))
            merged_ranges.update(cov.get("ranges", {}))
        base["parameters"] = merged_parameters
        base["ranges"] = merged_ranges
        return base

    # Fallback: return as-is (GeoJSON FeatureCollection merge, etc.)
    return base


def _detect_media_type(data: dict[str, Any]) -> str:
    cov_type = data.get("type", "")
    if cov_type in ("Coverage", "CoverageCollection"):
        return COVJSON_MEDIA_TYPE
    if cov_type == "FeatureCollection":
        return GEOJSON_MEDIA_TYPE
    return JSON_MEDIA_TYPE


# ---------------------------------------------------------------------------
# Query endpoints
# ---------------------------------------------------------------------------

_COORDS_DESCRIPTION = "A Well Known Text (WKT) point geometry, e.g. `POINT(lon lat)`."


@router.get(
    "/position",
    summary="Position query",
    description=(
        "Returns data for the specified point location. "
        "Use `coords` to specify a WKT POINT geometry."
    ),
)
async def position_query(
    request: Request,
    collection_id: str,
    coords: str = Query(..., description=_COORDS_DESCRIPTION),
    parameter_name: str | None = Query(
        None,
        alias="parameter-name",
        description="Comma-separated list of virtual parameter names to return.",
    ),
    datetime: str = Query(
        ...,
        description="RFC 3339 datetime or interval (e.g. `2024-01-01T00:00:00Z` or `2024-01-01T00:00:00Z/2024-01-02T00:00:00Z`).",
    ),
    z: str | None = Query(None, description="Vertical level or range."),
    crs: str | None = Query(None, description="Target CRS."),
    f: str | None = Query("CoverageJSON", description="Output format."),
) -> Response:
    return await _run_query(
        "position",
        collection_id,
        parameter_name,
        {"coords": coords, "datetime": datetime, "z": z, "crs": crs, "f": f},
    )


@router.get(
    "/radius",
    summary="Radius query",
    description="Returns data within a specified radius around a point.",
)
async def radius_query(
    request: Request,
    collection_id: str,
    coords: str = Query(..., description=_COORDS_DESCRIPTION),
    within: float = Query(..., description="Radius distance value."),
    within_units: str = Query(
        "km", description="Radius distance units (e.g. `km`, `m`)."
    ),
    parameter_name: str | None = Query(None, alias="parameter-name"),
    datetime: str = Query(..., description="RFC 3339 datetime or interval."),
    z: str | None = Query(None),
    crs: str | None = Query(None),
    f: str | None = Query("CoverageJSON"),
) -> Response:
    return await _run_query(
        "radius",
        collection_id,
        parameter_name,
        {
            "coords": coords,
            "within": within,
            "within-units": within_units,
            "datetime": datetime,
            "z": z,
            "crs": crs,
            "f": f,
        },
    )


@router.get(
    "/area",
    summary="Area query",
    description="Returns data within a specified area (WKT POLYGON).",
)
async def area_query(
    request: Request,
    collection_id: str,
    coords: str = Query(..., description="WKT POLYGON geometry."),
    parameter_name: str | None = Query(None, alias="parameter-name"),
    datetime: str = Query(..., description="RFC 3339 datetime or interval."),
    z: str | None = Query(None),
    resolution_x: float | None = Query(None, alias="resolution-x"),
    resolution_y: float | None = Query(None, alias="resolution-y"),
    crs: str | None = Query(None),
    f: str | None = Query("CoverageJSON"),
) -> Response:
    return await _run_query(
        "area",
        collection_id,
        parameter_name,
        {
            "coords": coords,
            "datetime": datetime,
            "z": z,
            "resolution-x": resolution_x,
            "resolution-y": resolution_y,
            "crs": crs,
            "f": f,
        },
    )


@router.get(
    "/cube",
    summary="Cube query",
    description="Returns data within a 2D bounding box (and optional vertical extent).",
)
async def cube_query(
    request: Request,
    collection_id: str,
    bbox: str = Query(
        ...,
        description="Bounding box as `minLon,minLat,maxLon,maxLat` (or with z: `minLon,minLat,minZ,maxLon,maxLat,maxZ`).",
    ),
    parameter_name: str | None = Query(None, alias="parameter-name"),
    datetime: str = Query(..., description="RFC 3339 datetime or interval."),
    z: str | None = Query(None),
    crs: str | None = Query(None),
    f: str | None = Query("CoverageJSON"),
) -> Response:
    return await _run_query(
        "cube",
        collection_id,
        parameter_name,
        {"bbox": bbox, "datetime": datetime, "z": z, "crs": crs, "f": f},
    )


@router.get(
    "/locations",
    summary="Locations query",
    description="Returns a list of available locations within the collection.",
)
async def locations_query(
    request: Request,
    collection_id: str,
    bbox: str | None = Query(None),
    datetime: str | None = Query(None),
    parameter_name: str | None = Query(None, alias="parameter-name"),
    crs: str | None = Query(None),
    f: str | None = Query("GeoJSON"),
) -> Response:
    return await _run_query(
        "locations",
        collection_id,
        parameter_name,
        {"bbox": bbox, "datetime": datetime, "crs": crs, "f": f},
    )


@router.get(
    "/locations/{location_id}",
    summary="Location query",
    description="Returns data for a specific named location.",
)
async def location_query(
    request: Request,
    collection_id: str,
    location_id: str,
    parameter_name: str | None = Query(None, alias="parameter-name"),
    datetime: str | None = Query(None),
    crs: str | None = Query(None),
    f: str | None = Query("CoverageJSON"),
) -> Response:
    logger.debug(
        "Location query collection='%s' location='%s' parameter_name=%s",
        collection_id,
        location_id,
        parameter_name or "<all>",
    )
    _check_collection(collection_id)
    cfg = get_virtual_collection_config()
    grouped = _resolve_parameters(parameter_name, cfg)

    results = []
    upstream_params_per_group = []
    for (upstream_collection, _dims_key), upstream_params in grouped.items():
        extra_params: dict[str, Any] = {"datetime": datetime, "crs": crs, "f": f}
        # Exclude 'level' from the upstream filter: the config may specify a
        # range (e.g. "1.5/2") or an exact value (e.g. "10"), but each station
        # reports measurements at its own actual height.  Filtering by level
        # would silently return no data whenever the station's exact level
        # differs from the configured value.  We filter by standard_name and,
        # where configured, by method and duration instead; the compound key
        # (e.g. "air_temperature:2.0:point:PT0S") is then matched back to the
        # correct virtual parameter via metocean:standard_name in the remap step.
        custom_dims = (
            {
                k: v
                for k, v in upstream_params[0][1].custom_dimensions.items()
                if k != "level"
            }
            if upstream_params
            else {}
        )
        params = {**extra_params, **custom_dims}
        params = {k: v for k, v in params.items() if v is not None}
        path = f"/collections/{upstream_collection}/locations/{location_id}"
        logger.debug("Proxying location/%s → %s params=%s", location_id, path, params)
        try:
            data = await upstream_get(path, params=params)
        except ValueError:
            logger.debug(
                "Data not found for location/%s → %s params=%s",
                location_id,
                path,
                params,
            )
            continue

        if data:
            results.append(data.json())
        upstream_params_per_group.append(upstream_params)

    if not results:
        raise HTTPException(status_code=404, detail="Location not found.")

    remapped = [
        _rewrite_parameter_metadata(_remap_coverage_parameter_names(r, p), p)
        for r, p in zip(results, upstream_params_per_group)
    ]
    merged = remapped[0] if len(remapped) == 1 else _merge_coverages(remapped)
    return JSONResponse(content=merged)


@router.get(
    "/items",
    summary="Items query",
    description=(
        "Returns observation features as GeoJSON FeatureCollection. "
        "Complies with OGC API — Features."
    ),
)
async def items_query(
    request: Request,
    collection_id: str,
    bbox: str | None = Query(None, description="Bounding box filter."),
    datetime: str = Query(..., description="RFC 3339 datetime or interval."),
    limit: int | None = Query(None, ge=1, le=10000),
    offset: int | None = Query(None, ge=0),
    parameter_name: str | None = Query(None, alias="parameter-name"),
    f: str | None = Query("GeoJSON"),
) -> Response:
    return await _run_query(
        "items",
        collection_id,
        parameter_name,
        {"bbox": bbox, "datetime": datetime, "limit": limit, "offset": offset, "f": f},
    )


@router.get(
    "/items/{item_id}",
    summary="Item query",
    description="Returns a single observation feature by its ID.",
)
async def item_query(
    request: Request,
    collection_id: str,
    item_id: str,
    f: str | None = Query("GeoJSON"),
) -> Response:
    logger.debug("Item query collection='%s' item='%s'", collection_id, item_id)
    _check_collection(collection_id)
    cfg = get_virtual_collection_config()
    # Items by ID use the first upstream collection (items are not parameter-specific)
    first_upstream = next(iter(cfg.values())).upstream_collection
    params: dict[str, Any] = {"f": f}
    params = {k: v for k, v in params.items() if v is not None}
    path = f"/collections/{first_upstream}/items/{item_id}"
    data = await upstream_get(path, params=params)
    media_type = _detect_media_type(data)
    return Response(content=json.dumps(data), media_type=media_type)
