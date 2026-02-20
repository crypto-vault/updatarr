import logging
from datetime import datetime

from .config import load_config, ListMapping
from .radarr import RadarrClient
from .mdblist import MDBListClient
from .database import add_history_entry

logger = logging.getLogger("updatarr.sync")

_sync_running = False
_last_sync: dict = {"time": None, "stats": None}


def get_sync_status():
    return {"running": _sync_running, "last": _last_sync}


async def run_sync():
    global _sync_running, _last_sync
    if _sync_running:
        logger.warning("Sync already running, skipping.")
        return

    _sync_running = True
    stats = {"updated": 0, "added": 0, "skipped": 0, "errors": 0}
    logger.info("=== Updatarr sync started ===")

    try:
        config = load_config()
        radarr = RadarrClient(config.radarr.url, config.radarr.api_key)
        mdblist = MDBListClient(config.mdblist.api_key)

        # Fetch Radarr state once
        quality_profiles = await radarr.get_quality_profiles()
        profile_map = {p["name"].lower(): p["id"] for p in quality_profiles}

        all_movies = await radarr.get_movies()
        tmdb_to_movie = {m["tmdbId"]: m for m in all_movies}

        root_folders = await radarr.get_root_folders()
        default_root = root_folders[0]["path"] if root_folders else "/"

        for list_cfg in config.lists:
            await _sync_list(
                list_cfg, radarr, mdblist,
                profile_map, tmdb_to_movie, default_root, stats
            )

    except Exception as e:
        logger.error(f"Sync failed: {e}", exc_info=True)
        stats["errors"] += 1
    finally:
        _sync_running = False
        _last_sync = {"time": datetime.utcnow().isoformat(), "stats": stats}
        logger.info(f"=== Sync complete: {stats} ===")


async def _sync_list(
    list_cfg: ListMapping,
    radarr: RadarrClient,
    mdblist: MDBListClient,
    profile_map: dict,
    tmdb_to_movie: dict,
    default_root: str,
    stats: dict,
):
    list_name = list_cfg.list_name or list_cfg.list_id
    logger.info(f"Processing list: {list_name} ({list_cfg.list_id}) → profile '{list_cfg.quality_profile}'")

    target_profile_id = profile_map.get(list_cfg.quality_profile.lower())
    if target_profile_id is None:
        logger.error(f"Quality profile '{list_cfg.quality_profile}' not found in Radarr. Available: {list(profile_map.keys())}")
        return

    try:
        items = await mdblist.get_list_items(list_cfg.list_id)
        logger.info(f"  {len(items)} movies in list")
    except Exception as e:
        logger.error(f"  Failed to fetch list {list_cfg.list_id}: {e}")
        return

    for item in items:
        tmdb_id = item.get("tmdb_id") or item.get("tmdbid")
        title = item.get("title", f"TMDB:{tmdb_id}")

        if not tmdb_id:
            logger.warning(f"  Skipping item with no TMDB ID: {item}")
            continue

        try:
            if tmdb_id in tmdb_to_movie:
                movie = tmdb_to_movie[tmdb_id]
                if movie["qualityProfileId"] == target_profile_id:
                    logger.debug(f"  SKIP {title} — already on correct profile")
                    stats["skipped"] += 1
                    await add_history_entry(list_cfg.list_id, list_name, title, tmdb_id, "skipped", "Already correct profile")
                else:
                    old_profile_id = movie["qualityProfileId"]
                    movie["qualityProfileId"] = target_profile_id
                    await radarr.update_movie(movie)
                    logger.info(f"  UPDATED {title} (profile {old_profile_id} → {target_profile_id})")
                    stats["updated"] += 1
                    await add_history_entry(list_cfg.list_id, list_name, title, tmdb_id, "updated",
                                            f"Profile changed to '{list_cfg.quality_profile}'")
            elif list_cfg.add_missing:
                root = list_cfg.root_folder or default_root
                await radarr.add_movie(
                    tmdb_id=tmdb_id,
                    quality_profile_id=target_profile_id,
                    root_folder=root,
                    monitored=list_cfg.monitored,
                    minimum_availability=list_cfg.minimum_availability,
                    search_on_add=list_cfg.search_on_add,
                )
                logger.info(f"  ADDED {title}")
                stats["added"] += 1
                await add_history_entry(list_cfg.list_id, list_name, title, tmdb_id, "added",
                                        f"Added with profile '{list_cfg.quality_profile}'")
            else:
                logger.debug(f"  NOT IN RADARR {title} — add_missing=false, skipping")
                stats["skipped"] += 1

        except Exception as e:
            logger.error(f"  ERROR processing {title}: {e}")
            stats["errors"] += 1
            await add_history_entry(list_cfg.list_id, list_name, title, tmdb_id, "error", str(e))
