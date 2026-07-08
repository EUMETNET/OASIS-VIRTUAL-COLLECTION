"""Application settings and virtual collection configuration loader."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic import HttpUrl
from pydantic import field_validator
from pydantic_settings import BaseSettings


class UnitSymbol(BaseModel):
    value: str
    type: str


class UnitConfig(BaseModel):
    label: str
    symbol: UnitSymbol | None = None


class ObservedPropertyConfig(BaseModel):
    id: str
    label: str


class ParameterConfig(BaseModel):
    """Configuration for a single virtual parameter."""

    upstream_collection: str
    title: str
    description: str = ""
    unit: UnitConfig
    observed_property: ObservedPropertyConfig
    data_type: str = "float"
    custom_dimensions: dict[str, str] = {}


VirtualCollectionConfig = dict[str, ParameterConfig]


class Settings(BaseSettings):
    upstream_edr_base_url: str = "https://observations.meteogate.eu"
    upstream_edr_api_key: str = ""
    virtual_collection_id: str = "virtual"
    virtual_collection_title: str = "OASIS Virtual Observations Collection"
    virtual_collection_description: str = (
        "A virtual collection providing access to meteorological observations "
        "from the Meteogate EDR API"
    )
    api_base_url: str = "http://localhost:8000"
    config_file: str = "config/virtual_collections.json"
    access_log_filter_paths: list[str] = ["/health"]

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_virtual_collection_config() -> VirtualCollectionConfig:
    settings = get_settings()
    config_path = Path(settings.config_file)
    if not config_path.is_absolute():
        # Resolve relative to the project root (parent of this file's package)
        config_path = Path(__file__).parent.parent / config_path
    raw: dict[str, Any] = json.loads(config_path.read_text())
    # Strip comment keys
    return {
        k: ParameterConfig(**v)
        for k, v in raw.items()
        if not k.startswith("_")
    }
