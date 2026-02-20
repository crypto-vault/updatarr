from pathlib import Path
from typing import Optional
import yaml
from pydantic import BaseModel


class RadarrConfig(BaseModel):
    url: str
    api_key: str


class MDBListConfig(BaseModel):
    api_key: str


class ListMapping(BaseModel):
    list_id: str
    list_name: Optional[str] = None
    quality_profile: str
    add_missing: bool = False
    root_folder: Optional[str] = None
    minimum_availability: str = "released"
    monitored: bool = True
    search_on_add: bool = False


class AppConfig(BaseModel):
    radarr: RadarrConfig
    mdblist: MDBListConfig
    schedule: Optional[str] = "0 4 * * *"
    lists: list[ListMapping] = []


CONFIG_PATH = Path("/config/updatarr.yml")
_FALLBACK_PATH = Path("updatarr.yml")


def load_config() -> AppConfig:
    path = CONFIG_PATH if CONFIG_PATH.exists() else _FALLBACK_PATH
    if not path.exists():
        raise FileNotFoundError(f"Config file not found at {path}. See updatarr.example.yml.")
    with open(path) as f:
        raw = yaml.safe_load(f)
    return AppConfig(**raw)
