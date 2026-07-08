"""
Tests for the OASIS Virtual Collection EDR API.

Uses httpx's ASGI transport to test endpoints without a live server.
Upstream proxy calls to meteogate are mocked with respx where needed.
"""

from __future__ import annotations

import json
import logging
import pytest
from httpx import ASGITransport
from httpx import AsyncClient

from app.main import app
from app.config import get_settings


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def client():
    """ASGI test client wired directly to the FastAPI app."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


async def test_health(client: AsyncClient):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Landing page
# ---------------------------------------------------------------------------


async def test_landing_page_status(client: AsyncClient):
    response = await client.get("/")
    assert response.status_code == 200


async def test_landing_page_has_required_links(client: AsyncClient):
    data = (await client.get("/")).json()
    rels = {link["rel"] for link in data["links"]}
    assert "self" in rels
    assert "conformance" in rels
    assert "data" in rels
    assert "service-desc" in rels


async def test_landing_page_title(client: AsyncClient):
    settings = get_settings()
    data = (await client.get("/")).json()
    assert data["title"] == settings.virtual_collection_title


# ---------------------------------------------------------------------------
# Conformance
# ---------------------------------------------------------------------------


async def test_conformance_status(client: AsyncClient):
    response = await client.get("/conformance")
    assert response.status_code == 200


async def test_conformance_contains_edr_core(client: AsyncClient):
    data = (await client.get("/conformance")).json()
    assert "conformsTo" in data
    classes = data["conformsTo"]
    assert any("ogcapi-edr" in c for c in classes)


async def test_conformance_contains_position(client: AsyncClient):
    data = (await client.get("/conformance")).json()
    assert any("position" in c for c in data["conformsTo"])


# ---------------------------------------------------------------------------
# Collections metadata
# ---------------------------------------------------------------------------


async def test_collections_status(client: AsyncClient):
    response = await client.get("/collections")
    assert response.status_code == 200


async def test_collections_contains_virtual(client: AsyncClient):
    settings = get_settings()
    data = (await client.get("/collections")).json()
    ids = [c["id"] for c in data["collections"]]
    assert settings.virtual_collection_id in ids


async def test_collection_detail_status(client: AsyncClient):
    settings = get_settings()
    response = await client.get(f"/collections/{settings.virtual_collection_id}")
    assert response.status_code == 200


async def test_collection_404_for_unknown(client: AsyncClient):
    response = await client.get("/collections/does_not_exist")
    assert response.status_code == 404


async def test_collection_has_parameter_names(client: AsyncClient):
    settings = get_settings()
    data = (await client.get(f"/collections/{settings.virtual_collection_id}")).json()
    assert "parameter_names" in data
    assert len(data["parameter_names"]) > 0


async def test_collection_has_all_query_types(client: AsyncClient):
    settings = get_settings()
    data = (await client.get(f"/collections/{settings.virtual_collection_id}")).json()
    dq = data["data_queries"]
    for query_type in ("position", "radius", "area", "cube", "trajectory", "corridor", "locations", "items"):
        assert query_type in dq, f"Missing query type: {query_type}"


async def test_collection_has_extent(client: AsyncClient):
    settings = get_settings()
    data = (await client.get(f"/collections/{settings.virtual_collection_id}")).json()
    assert "extent" in data
    assert "spatial" in data["extent"]


async def test_collection_parameters_have_required_fields(client: AsyncClient):
    settings = get_settings()
    data = (await client.get(f"/collections/{settings.virtual_collection_id}")).json()
    for param_name, param in data["parameter_names"].items():
        assert "observedProperty" in param, f"Missing observedProperty in {param_name}"
        assert "unit" in param, f"Missing unit in {param_name}"


async def test_collection_crs_defined(client: AsyncClient):
    settings = get_settings()
    data = (await client.get(f"/collections/{settings.virtual_collection_id}")).json()
    assert "crs" in data
    assert len(data["crs"]) > 0


async def test_collection_output_formats_defined(client: AsyncClient):
    settings = get_settings()
    data = (await client.get(f"/collections/{settings.virtual_collection_id}")).json()
    assert "output_formats" in data
    assert "CoverageJSON" in data["output_formats"]


# ---------------------------------------------------------------------------
# Config / parameter validation
# ---------------------------------------------------------------------------


async def test_all_configured_parameters_exposed(client: AsyncClient):
    """Every parameter in virtual_collections.json must appear in the API."""
    from app.config import get_virtual_collection_config

    settings = get_settings()
    cfg = get_virtual_collection_config()
    data = (await client.get(f"/collections/{settings.virtual_collection_id}")).json()
    exposed = set(data["parameter_names"].keys())
    for name in cfg:
        assert name in exposed, f"Parameter '{name}' not exposed in collection"


# ---------------------------------------------------------------------------
# EDR query endpoints — 400/404 without upstream (no live server)
# ---------------------------------------------------------------------------


async def test_position_missing_coords_returns_422(client: AsyncClient):
    settings = get_settings()
    response = await client.get(
        f"/collections/{settings.virtual_collection_id}/position"
    )
    # coords is required → FastAPI validation error
    assert response.status_code == 422


async def test_position_unknown_parameter_returns_400_or_502(client: AsyncClient):
    """Unknown parameter-name should be caught before hitting upstream."""
    settings = get_settings()
    response = await client.get(
        f"/collections/{settings.virtual_collection_id}/position",
        params={"coords": "POINT(10 55)", "datetime": "2024-01-01T00:00:00Z", "parameter-name": "non_existent_param"},
    )
    # Our validation raises 400 before the upstream proxy is called
    assert response.status_code == 400


async def test_position_missing_datetime_returns_422(client: AsyncClient):
    settings = get_settings()
    response = await client.get(
        f"/collections/{settings.virtual_collection_id}/position",
        params={"coords": "POINT(10 55)"},
    )
    assert response.status_code == 422


async def test_location_datetime_is_optional(client: AsyncClient):
    """datetime must not be marked required for the single-location endpoint."""
    data = (await client.get("/api")).json()
    params = data["paths"]["/collections/{collection_id}/locations/{location_id}"]["get"]["parameters"]
    datetime_param = next((p for p in params if p["name"] == "datetime"), None)
    assert datetime_param is not None
    assert datetime_param.get("required", False) is False


async def test_data_query_datetime_is_required(client: AsyncClient):
    """datetime must be marked required for every spatial data query endpoint."""
    data = (await client.get("/api")).json()
    settings = get_settings()
    cid = settings.virtual_collection_id
    required_paths = [
        f"/collections/{{collection_id}}/position",
        f"/collections/{{collection_id}}/radius",
        f"/collections/{{collection_id}}/area",
        f"/collections/{{collection_id}}/cube",
        f"/collections/{{collection_id}}/trajectory",
        f"/collections/{{collection_id}}/corridor",
        f"/collections/{{collection_id}}/items",
    ]
    for path in required_paths:
        params = data["paths"][path]["get"]["parameters"]
        datetime_param = next((p for p in params if p["name"] == "datetime"), None)
        assert datetime_param is not None, f"datetime param missing on {path}"
        assert datetime_param.get("required") is True, f"datetime not required on {path}"


async def test_radius_missing_within_returns_422(client: AsyncClient):
    settings = get_settings()
    response = await client.get(
        f"/collections/{settings.virtual_collection_id}/radius",
        params={"coords": "POINT(10 55)"},
    )
    assert response.status_code == 422


async def test_query_on_unknown_collection_returns_404(client: AsyncClient):
    response = await client.get(
        "/collections/unknown/position",
        params={"coords": "POINT(10 55)", "datetime": "2024-01-01T00:00:00Z"},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# OpenAPI spec
# ---------------------------------------------------------------------------


async def test_openapi_json_accessible(client: AsyncClient):
    response = await client.get("/api")
    assert response.status_code == 200
    data = response.json()
    assert "openapi" in data
    assert "paths" in data


async def test_openapi_includes_edr_paths(client: AsyncClient):
    data = (await client.get("/api")).json()
    paths = data["paths"]
    # Should have at minimum: /, /conformance, /collections, /collections/{id}
    assert "/" in paths
    assert "/conformance" in paths
    assert "/collections" in paths


# ---------------------------------------------------------------------------
# Unit tests: data transformation helpers
# ---------------------------------------------------------------------------


def test_build_covjson_parameter_basic():
    """_build_covjson_parameter produces a well-formed CoverageJSON Parameter object."""
    from app.config import ParameterConfig, UnitConfig, UnitSymbol, ObservedPropertyConfig
    from app.routers.edr_queries import _build_covjson_parameter

    cfg = ParameterConfig(
        upstream_collection="observations",
        title="Air Temperature",
        description="Air temperature at 2 m",
        unit=UnitConfig(
            label="degree Celsius",
            symbol=UnitSymbol(value="Cel", type="http://www.opengis.net/def/uom/UCUM/"),
        ),
        observed_property=ObservedPropertyConfig(
            id="http://vocab.nerc.ac.uk/standard_name/air_temperature/",
            label="Air Temperature",
        ),
        data_type="float",
    )
    result = _build_covjson_parameter("air_temperature_2_m", cfg)

    assert result["type"] == "Parameter"
    assert result["description"] == "Air temperature at 2 m"
    assert result["dataType"] == "float"
    assert result["observedProperty"]["id"] == "http://vocab.nerc.ac.uk/standard_name/air_temperature/"
    assert result["observedProperty"]["label"] == "Air Temperature"
    assert result["unit"]["label"] == "degree Celsius"
    assert result["unit"]["symbol"]["value"] == "Cel"
    assert result["unit"]["symbol"]["type"] == "http://www.opengis.net/def/uom/UCUM/"


def test_build_covjson_parameter_no_description():
    """A ParameterConfig with no description omits the key entirely."""
    from app.config import ParameterConfig, UnitConfig, ObservedPropertyConfig
    from app.routers.edr_queries import _build_covjson_parameter

    cfg = ParameterConfig(
        upstream_collection="observations",
        title="Wind Speed",
        unit=UnitConfig(label="m/s"),
        observed_property=ObservedPropertyConfig(
            id="http://example.com/wind_speed",
            label="Wind Speed",
        ),
    )
    result = _build_covjson_parameter("wind_speed", cfg)
    assert "description" not in result
    assert "symbol" not in result["unit"]


def test_rewrite_parameter_metadata_coverage():
    """_rewrite_parameter_metadata replaces upstream metadata in a Coverage."""
    from app.config import ParameterConfig, UnitConfig, UnitSymbol, ObservedPropertyConfig
    from app.routers.edr_queries import _rewrite_parameter_metadata

    cfg = ParameterConfig(
        upstream_collection="observations",
        title="Air Temperature",
        description="Air temperature at 2 m",
        unit=UnitConfig(
            label="degree Celsius",
            symbol=UnitSymbol(value="Cel", type="http://www.opengis.net/def/uom/UCUM/"),
        ),
        observed_property=ObservedPropertyConfig(
            id="http://vocab.nerc.ac.uk/standard_name/air_temperature/",
            label="Air Temperature",
        ),
        data_type="float",
    )
    # Simulate a Coverage already key-remapped by _remap_coverage_parameter_names
    coverage = {
        "type": "Coverage",
        "parameters": {
            "air_temperature_2_m": {
                "type": "Parameter",
                "unit": {"label": {"en": "K"}},  # upstream used Kelvin label
                "observedProperty": {"label": {"en": "Air temperature"}},
            }
        },
        "ranges": {"air_temperature_2_m": {"type": "NdArray", "values": [293.15]}},
    }

    result = _rewrite_parameter_metadata(coverage, [("air_temperature_2_m", cfg)])

    param = result["parameters"]["air_temperature_2_m"]
    # Metadata should now be our canonical form, not the upstream form
    assert param["unit"]["label"] == "degree Celsius"
    assert param["unit"]["symbol"]["value"] == "Cel"
    assert param["observedProperty"]["label"] == "Air Temperature"
    assert param["dataType"] == "float"
    # Ranges are untouched
    assert result["ranges"]["air_temperature_2_m"]["values"] == [293.15]


def test_rewrite_parameter_metadata_coverage_collection():
    """_rewrite_parameter_metadata handles CoverageCollection top-level and per-coverage params."""
    from app.config import ParameterConfig, UnitConfig, ObservedPropertyConfig
    from app.routers.edr_queries import _rewrite_parameter_metadata

    cfg = ParameterConfig(
        upstream_collection="observations",
        title="Relative Humidity",
        description="Relative humidity at 2 m",
        unit=UnitConfig(label="percent"),
        observed_property=ObservedPropertyConfig(
            id="http://vocab.nerc.ac.uk/standard_name/relative_humidity/",
            label="Relative Humidity",
        ),
        data_type="float",
    )
    coverage_collection = {
        "type": "CoverageCollection",
        "parameters": {
            "relative_humidity": {"type": "Parameter", "unit": {"label": "pct"}}
        },
        "coverages": [
            {
                "type": "Coverage",
                "parameters": {
                    "relative_humidity": {"type": "Parameter", "unit": {"label": "pct"}}
                },
                "ranges": {"relative_humidity": {"values": [85.0]}},
            }
        ],
    }

    result = _rewrite_parameter_metadata(
        coverage_collection, [("relative_humidity", cfg)]
    )

    # Top-level parameters rewritten
    assert result["parameters"]["relative_humidity"]["unit"]["label"] == "percent"
    # Per-coverage parameters also rewritten
    assert result["coverages"][0]["parameters"]["relative_humidity"]["unit"]["label"] == "percent"
    # Ranges untouched
    assert result["coverages"][0]["ranges"]["relative_humidity"]["values"] == [85.0]


def test_rewrite_parameter_metadata_leaves_unknown_params_intact():
    """Parameters not in our config are left unchanged."""
    from app.config import ParameterConfig, UnitConfig, ObservedPropertyConfig
    from app.routers.edr_queries import _rewrite_parameter_metadata

    cfg = ParameterConfig(
        upstream_collection="observations",
        title="Wind Speed",
        unit=UnitConfig(label="m/s"),
        observed_property=ObservedPropertyConfig(
            id="http://example.com/wind_speed", label="Wind Speed"
        ),
    )
    coverage = {
        "type": "Coverage",
        "parameters": {
            "wind_speed": {"type": "Parameter", "unit": {"label": "knots"}},
            "some_other_param": {"type": "Parameter", "unit": {"label": "hPa"}},
        },
        "ranges": {},
    }

    result = _rewrite_parameter_metadata(coverage, [("wind_speed", cfg)])

    # Our parameter is rewritten
    assert result["parameters"]["wind_speed"]["unit"]["label"] == "m/s"
    # Unknown parameter is untouched
    assert result["parameters"]["some_other_param"]["unit"]["label"] == "hPa"


def test_rewrite_parameter_metadata_noop_for_geojson():
    """A FeatureCollection without a top-level parameters key is returned unchanged."""
    from app.config import ParameterConfig, UnitConfig, ObservedPropertyConfig
    from app.routers.edr_queries import _rewrite_parameter_metadata

    cfg = ParameterConfig(
        upstream_collection="observations",
        title="Wind Speed",
        unit=UnitConfig(label="m/s"),
        observed_property=ObservedPropertyConfig(
            id="http://example.com/wind_speed", label="Wind Speed"
        ),
    )
    feature_collection = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {"wind_speed": 5.2}}],
    }
    result = _rewrite_parameter_metadata(feature_collection, [("wind_speed", cfg)])
    # Unchanged — no top-level parameters dict present
    assert result == feature_collection


def test_remap_and_rewrite_feature_collection_parameters():
    """Key remap and metadata rewrite both apply to a GeoJSON FeatureCollection
    that carries a top-level parameters section (as returned by the locations endpoint)."""
    from app.config import ParameterConfig, UnitConfig, UnitSymbol, ObservedPropertyConfig
    from app.routers.edr_queries import (
        _remap_coverage_parameter_names,
        _rewrite_parameter_metadata,
    )

    cfg = ParameterConfig(
        upstream_collection="observations",
        title="Air Temperature",
        description="Air temperature at 2 m",
        unit=UnitConfig(
            label="degree Celsius",
            symbol=UnitSymbol(value="Cel", type="http://www.opengis.net/def/uom/UCUM/"),
        ),
        observed_property=ObservedPropertyConfig(
            id="http://vocab.nerc.ac.uk/standard_name/air_temperature/",
            label="Air Temperature",
        ),
        data_type="float",
        custom_dimensions={"standard_name": "air_temperature"},
    )

    # Upstream uses a compound key; metocean:standard_name drives matching
    upstream_key = "air_temperature:2.0:point:PT0S"
    feature_collection = {
        "type": "FeatureCollection",
        "parameters": {
            upstream_key: {
                "type": "Parameter",
                "unit": {"label": {"en": "K"}},
                "observedProperty": {"label": {"en": "Air temperature"}},
                "metocean:standard_name": "air_temperature",
            }
        },
        "features": [
            {"type": "Feature", "properties": {upstream_key: 293.15}}
        ],
    }

    upstream_params = [("air_temperature_2_m", cfg)]

    # Step 1: remap upstream compound key -> virtual name
    remapped = _remap_coverage_parameter_names(feature_collection, upstream_params)
    assert "air_temperature_2_m" in remapped["parameters"]
    assert upstream_key not in remapped["parameters"]

    # Step 2: rewrite metadata to canonical form
    result = _rewrite_parameter_metadata(remapped, upstream_params)
    param = result["parameters"]["air_temperature_2_m"]
    assert param["unit"]["label"] == "degree Celsius"
    assert param["unit"]["symbol"]["value"] == "Cel"
    assert param["observedProperty"]["label"] == "Air Temperature"
    assert param["dataType"] == "float"
    # Features are untouched
    assert result["features"][0]["properties"][upstream_key] == 293.15


def test_remap_coverage_collection_compound_keys():
    """CoverageCollection with realistic compound upstream keys is fully remapped.

    Mirrors the actual Meteogate response shape where both the top-level
    parameters block and each coverage's parameters/ranges use the compound key
    format 'standard_name:level:method:duration'.
    """
    from app.config import ParameterConfig, UnitConfig, ObservedPropertyConfig
    from app.routers.edr_queries import _remap_coverage_parameter_names

    cfg = ParameterConfig(
        upstream_collection="observations",
        title="Air Temperature",
        unit=UnitConfig(label="degree Celsius"),
        observed_property=ObservedPropertyConfig(
            id="http://vocab.nerc.ac.uk/standard_name/air_temperature/",
            label="Air Temperature",
        ),
        custom_dimensions={"standard_name": "air_temperature", "level": "1.5/2", "method": "point", "duration": "PT0S"},
    )

    upstream_key = "air_temperature:2.0:point:PT0S"
    upstream_param_obj = {
        "type": "Parameter",
        "observedProperty": {"label": {"en": "Air temperature"}},
        "unit": {"label": {"en": "Cel"}},
        "metocean:standard_name": "air_temperature",
        "metocean:level": 2.0,
    }

    coverage_collection = {
        "type": "CoverageCollection",
        "parameters": {upstream_key: upstream_param_obj},
        "coverages": [
            {
                "type": "Coverage",
                "parameters": {upstream_key: upstream_param_obj},
                "ranges": {
                    upstream_key: {"type": "NdArray", "values": [19.2, 18.9]}
                },
            },
            {
                "type": "Coverage",
                "parameters": {upstream_key: upstream_param_obj},
                "ranges": {
                    upstream_key: {"type": "NdArray", "values": [21.1, 20.5]}
                },
            },
        ],
    }

    result = _remap_coverage_parameter_names(coverage_collection, [("air_temperature_2_m", cfg)])

    # Top-level parameters remapped
    assert "air_temperature_2_m" in result["parameters"]
    assert upstream_key not in result["parameters"]

    # Both coverages remapped in parameters and ranges
    for cov in result["coverages"]:
        assert "air_temperature_2_m" in cov["parameters"]
        assert upstream_key not in cov["parameters"]
        assert "air_temperature_2_m" in cov["ranges"]
        assert upstream_key not in cov["ranges"]
        # Data values untouched
        assert cov["ranges"]["air_temperature_2_m"]["type"] == "NdArray"


def test_remap_feature_collection_parameter_name_list():
    """Each feature's properties['parameter-name'] list is remapped from the
    upstream compound key to the virtual name.

    This covers the case where the config specifies a level *range* (e.g.
    '1.5/2') but the upstream reports the resolved exact level (e.g. 2.0) in
    the compound key.  The mapping is driven by metocean:standard_name so the
    range vs exact level difference is transparent.
    """
    from app.config import ParameterConfig, UnitConfig, ObservedPropertyConfig
    from app.routers.edr_queries import _remap_coverage_parameter_names

    cfg = ParameterConfig(
        upstream_collection="observations",
        title="Air Temperature",
        unit=UnitConfig(label="degree Celsius"),
        observed_property=ObservedPropertyConfig(
            id="http://vocab.nerc.ac.uk/standard_name/air_temperature/",
            label="Air Temperature",
        ),
        # Config uses a level range; upstream will resolve to an exact level
        custom_dimensions={
            "standard_name": "air_temperature",
            "level": "1.5/2",
            "method": "point",
            "duration": "PT0S",
        },
    )

    # Upstream key uses the resolved exact level 2.0, not the requested range 1.5/2
    upstream_key = "air_temperature:2.0:point:PT0S"
    upstream_param_obj = {
        "type": "Parameter",
        "observedProperty": {"label": {"en": "Air temperature"}},
        "unit": {"label": {"en": "Cel"}},
        "metocean:standard_name": "air_temperature",
        "metocean:level": 2.0,
    }

    feature_collection = {
        "type": "FeatureCollection",
        "parameters": {upstream_key: upstream_param_obj},
        "features": [
            {
                "type": "Feature",
                "id": "0-20000-0-06260",
                "geometry": {"type": "Point", "coordinates": [5.1797, 52.0989]},
                "properties": {
                    "name": "De Bilt",
                    "parameter-name": [upstream_key],
                },
            },
            {
                "type": "Feature",
                "id": "0-20000-0-06275",
                "geometry": {"type": "Point", "coordinates": [5.8731, 52.0589]},
                "properties": {
                    "name": "Deelen Airport",
                    "parameter-name": [upstream_key],
                },
            },
        ],
    }

    result = _remap_coverage_parameter_names(feature_collection, [("air_temperature_2_m", cfg)])

    # Top-level parameters block remapped
    assert "air_temperature_2_m" in result["parameters"]
    assert upstream_key not in result["parameters"]

    # Every feature's parameter-name list remapped to the virtual name
    for feature in result["features"]:
        assert feature["properties"]["parameter-name"] == ["air_temperature_2_m"]

    # Non-parameter feature properties untouched
    assert result["features"][0]["properties"]["name"] == "De Bilt"
    assert result["features"][1]["properties"]["name"] == "Deelen Airport"


# ---------------------------------------------------------------------------
# Unit tests: upstream URL rewriting
# ---------------------------------------------------------------------------


def test_rewrite_upstream_urls_top_level_links():
    """Top-level links pointing at the upstream API are rewritten to our API."""
    from app.routers.edr_queries import _rewrite_upstream_urls
    from unittest.mock import patch
    from app.config import Settings

    settings = Settings(
        upstream_edr_base_url="https://upstream.example.com",
        api_base_url="http://localhost:8000",
        virtual_collection_id="virtual",
    )
    with patch("app.routers.edr_queries.get_settings", return_value=settings):
        data = {
            "type": "FeatureCollection",
            "links": [
                {"rel": "self", "href": "https://upstream.example.com/collections/observations/locations"},
                {"rel": "next", "href": "https://upstream.example.com/collections/observations/locations?offset=10"},
            ],
            "features": [],
        }
        result = _rewrite_upstream_urls(data, {"observations"})

    assert result["links"][0]["href"] == "http://localhost:8000/collections/virtual/locations"
    assert result["links"][1]["href"] == "http://localhost:8000/collections/virtual/locations?offset=10"


def test_rewrite_upstream_urls_feature_links():
    """Per-feature links pointing at the upstream API are rewritten to our API."""
    from app.routers.edr_queries import _rewrite_upstream_urls
    from unittest.mock import patch
    from app.config import Settings

    settings = Settings(
        upstream_edr_base_url="https://upstream.example.com",
        api_base_url="http://localhost:8000",
        virtual_collection_id="virtual",
    )
    with patch("app.routers.edr_queries.get_settings", return_value=settings):
        data = {
            "type": "FeatureCollection",
            "links": [],
            "features": [
                {
                    "type": "Feature",
                    "id": "station_001",
                    "links": [
                        {
                            "rel": "self",
                            "href": "https://upstream.example.com/collections/observations/locations/station_001",
                        },
                        {
                            "rel": "data",
                            "href": "https://upstream.example.com/collections/observations/locations/station_001/position",
                        },
                    ],
                    "properties": {},
                    "geometry": None,
                }
            ],
        }
        result = _rewrite_upstream_urls(data, {"observations"})

    feature_links = result["features"][0]["links"]
    assert feature_links[0]["href"] == "http://localhost:8000/collections/virtual/locations/station_001"
    assert feature_links[1]["href"] == "http://localhost:8000/collections/virtual/locations/station_001/position"


def test_rewrite_upstream_urls_non_upstream_links_unchanged():
    """Links that do not point at the upstream API are left as-is."""
    from app.routers.edr_queries import _rewrite_upstream_urls
    from unittest.mock import patch
    from app.config import Settings

    settings = Settings(
        upstream_edr_base_url="https://upstream.example.com",
        api_base_url="http://localhost:8000",
        virtual_collection_id="virtual",
    )
    with patch("app.routers.edr_queries.get_settings", return_value=settings):
        data = {
            "type": "FeatureCollection",
            "links": [
                {"rel": "describedby", "href": "https://some-other-service.com/metadata"},
            ],
            "features": [],
        }
        result = _rewrite_upstream_urls(data, {"observations"})

    assert result["links"][0]["href"] == "https://some-other-service.com/metadata"


def test_rewrite_upstream_urls_links_without_href_unchanged():
    """Link objects without an href key are passed through untouched."""
    from app.routers.edr_queries import _rewrite_upstream_urls
    from unittest.mock import patch
    from app.config import Settings

    settings = Settings(
        upstream_edr_base_url="https://upstream.example.com",
        api_base_url="http://localhost:8000",
        virtual_collection_id="virtual",
    )
    with patch("app.routers.edr_queries.get_settings", return_value=settings):
        data = {
            "type": "FeatureCollection",
            "links": [{"rel": "self"}],
            "features": [],
        }
        result = _rewrite_upstream_urls(data, {"observations"})

    assert result["links"][0] == {"rel": "self"}


# ---------------------------------------------------------------------------
# Unit tests: access-log endpoint filter
# ---------------------------------------------------------------------------


def _make_access_record(path: str) -> logging.LogRecord:
    """Create a LogRecord that mimics a uvicorn access-log entry."""
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:12345", "GET", path, "1.1", 200),
        exc_info=None,
    )
    return record


def test_endpoint_filter_suppresses_configured_path():
    """A request to a filtered path is suppressed (filter returns False)."""
    from app.logging_filters import EndpointFilter

    f = EndpointFilter(["/health"])
    assert f.filter(_make_access_record("/health")) is False


def test_endpoint_filter_passes_other_paths():
    """Requests to non-filtered paths are allowed through (filter returns True)."""
    from app.logging_filters import EndpointFilter

    f = EndpointFilter(["/health"])
    assert f.filter(_make_access_record("/collections")) is True
    assert f.filter(_make_access_record("/")) is True


def test_endpoint_filter_strips_query_string():
    """Query parameters do not prevent a filtered path from being suppressed."""
    from app.logging_filters import EndpointFilter

    f = EndpointFilter(["/health"])
    assert f.filter(_make_access_record("/health?verbose=1")) is False


def test_endpoint_filter_multiple_paths():
    """Multiple paths can be filtered at once."""
    from app.logging_filters import EndpointFilter

    f = EndpointFilter(["/health", "/metrics"])
    assert f.filter(_make_access_record("/health")) is False
    assert f.filter(_make_access_record("/metrics")) is False
    assert f.filter(_make_access_record("/collections")) is True


def test_endpoint_filter_empty_list_passes_all():
    """An empty filter list suppresses nothing."""
    from app.logging_filters import EndpointFilter

    f = EndpointFilter([])
    assert f.filter(_make_access_record("/health")) is True


def test_endpoint_filter_installed_on_uvicorn_access_logger():
    """main.py installs an EndpointFilter on the uvicorn.access logger."""
    from app.logging_filters import EndpointFilter

    uvicorn_logger = logging.getLogger("uvicorn.access")
    endpoint_filters = [f for f in uvicorn_logger.filters if isinstance(f, EndpointFilter)]
    assert len(endpoint_filters) >= 1


def test_access_log_filter_paths_default_contains_health():
    """The default setting filters /health out of the access log."""
    from app.config import Settings

    settings = Settings()
    assert "/health" in settings.access_log_filter_paths
