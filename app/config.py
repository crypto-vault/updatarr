from pathlib import Path
from typing import Optional, Any
import yaml
from pydantic import BaseModel, model_validator


class RadarrConfig(BaseModel):
    url: str
    api_key: str


class MDBListConfig(BaseModel):
    api_key: str


class PlexConfig(BaseModel):
    url: Optional[str] = None          # Local Plex URL e.g. http://plex:32400
    token: Optional[str] = None        # Plex token
    sync_own: bool = True              # Sync own watchlist RSS
    sync_friends: bool = False         # Sync friends watchlist RSS
    quality_profile: str
    add_missing: bool = False
    search_on_update: bool = False
    root_folder: Optional[str] = None
    minimum_availability: str = "released"
    monitored: bool = True
    search_on_add: bool = False
    enabled: bool = True


class ListMapping(BaseModel):
    list_id: str
    list_name: Optional[str] = None
    quality_profile: str
    enabled: bool = True
    add_missing: bool = False
    search_on_update: bool = False
    root_folder: Optional[str] = None
    minimum_availability: str = "released"
    monitored: bool = True
    search_on_add: bool = False


class OmbiConfig(BaseModel):
    url: str
    api_key: str
    quality_profile: str
    approved_only: bool = True
    add_missing: bool = False
    search_on_update: bool = False
    root_folder: Optional[str] = None
    minimum_availability: str = "released"
    monitored: bool = True
    search_on_add: bool = False
    enabled: bool = True


class RetirementStage(BaseModel):
    action: str = "redownload"   # "redownload" | "reencode" | "archive" | "delete"
    older_than_days: int = 730
    grace_days: int = 7
    quality_profile: str = ""    # only used by "redownload" action
    enabled: bool = True

    @model_validator(mode="after")
    def _migrate_tdarr_action(self) -> "RetirementStage":
        """Rename legacy action value 'tdarr' → 'reencode'."""
        if self.action == "tdarr":
            self.action = "reencode"
        return self


class RetirementConfig(BaseModel):
    enabled: bool = False
    date_source: str = "radarr"       # "radarr" or "plex"
    upgrade_threshold: bool = True    # block upgrades on movies older than threshold
    stages: list[RetirementStage] = []

    # ── Legacy flat fields (kept for YAML backward compat) ──────────────────
    # Old configs used a single method/profile/days. Auto-migrated to stages[0].
    quality_profile: Optional[str] = None
    older_than_days: Optional[int] = None
    grace_days: Optional[int] = None
    method: Optional[str] = None

    @model_validator(mode="after")
    def _migrate_legacy(self) -> "RetirementConfig":
        """Silently migrate single-stage flat config to the stages list."""
        if not self.stages and self.method:
            method = "reencode" if self.method == "tdarr" else self.method
            self.stages = [RetirementStage(
                action=method,
                older_than_days=self.older_than_days or 730,
                grace_days=self.grace_days or 7,
                quality_profile=self.quality_profile or "",
            )]
        return self


class TdarrConfig(BaseModel):
    url: str
    library_id: str
    path_replace_from: Optional[str] = None  # Radarr path prefix to replace
    path_replace_to: Optional[str] = None    # Tdarr path prefix to use instead


class ArchiveConfig(BaseModel):
    path: str                                # Archive destination as Updatarr sees it
    path_replace_from: Optional[str] = None  # Radarr-side path prefix to replace
    path_replace_to: Optional[str] = None    # Updatarr-accessible source path prefix


class AppConfig(BaseModel):
    radarr: RadarrConfig
    mdblist: Optional[MDBListConfig] = None
    plex: Optional[PlexConfig] = None
    ombi: Optional[OmbiConfig] = None
    downgrade: Optional[RetirementConfig] = None
    tdarr: Optional[TdarrConfig] = None
    archive: Optional[ArchiveConfig] = None
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


def get_config_path() -> Path:
    return CONFIG_PATH if CONFIG_PATH.parent.exists() and CONFIG_PATH.parent != Path(".") and CONFIG_PATH.exists() else _FALLBACK_PATH


def save_config(data: dict) -> None:
    path = CONFIG_PATH if CONFIG_PATH.parent.exists() and str(CONFIG_PATH.parent) != "." else _FALLBACK_PATH
    if data.get("mdblist") and not data["mdblist"].get("api_key"):
        data.pop("mdblist", None)
    if data.get("plex"):
        if not data["plex"].get("url") and not data["plex"].get("token"):
            data.pop("plex", None)
    if data.get("ombi"):
        if not data["ombi"].get("url") or not data["ombi"].get("api_key"):
            data.pop("ombi", None)
    if data.get("tdarr"):
        if not data["tdarr"].get("url") or not data["tdarr"].get("library_id"):
            data.pop("tdarr", None)
    if data.get("archive"):
        if not data["archive"].get("path"):
            data.pop("archive", None)
    if data.get("downgrade") and not data["downgrade"].get("enabled"):
        # Keep the block so settings are preserved, just leave enabled=false
        pass

    # Strip legacy flat fields from downgrade block — they've been migrated to stages
    if data.get("downgrade"):
        for legacy_key in ("quality_profile", "older_than_days", "grace_days", "method"):
            data["downgrade"].pop(legacy_key, None)

    def clean(obj):
        if isinstance(obj, dict):
            return {k: clean(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [clean(i) for i in obj]
        if obj == "":
            return None
        return obj

    data = clean(data)
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
