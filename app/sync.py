import logging
from datetime import datetime, timedelta

from .config import load_config, ListMapping, PlexConfig, OmbiConfig, DowngradeConfig
from .radarr import RadarrClient
from .mdblist import MDBListClient
from .plex import PlexRSSClient, fetch_plex_rss_urls
from .ombi import OmbiClient
from .tdarr import TdarrClient
from .database import (add_history_entry, queue_downgrade,
                       get_due_downgrades, get_pending_downgrades, update_downgrade_status)

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
    stats = {"updated": 0, "added": 0, "skipped": 0, "errors": 0,
             "downgrade_queued": 0, "downgrade_executed": 0}
    logger.info("=== Updatarr sync started ===")

    try:
        config = load_config()
        radarr = RadarrClient(config.radarr.url, config.radarr.api_key)

        quality_profiles = await radarr.get_quality_profiles()
        profile_map = {p["name"].lower(): p["id"] for p in quality_profiles}
        profile_id_to_name = {p["id"]: p["name"] for p in quality_profiles}

        all_movies = await radarr.get_movies()
        tmdb_to_movie = {m["tmdbId"]: m for m in all_movies}

        root_folders = await radarr.get_root_folders()
        default_root = root_folders[0]["path"] if root_folders else "/"

        # Build Plex added-date, resolution, and file-path maps if needed
        plex_added_map: dict[int, datetime] = {}
        plex_4k_map: dict[int, bool] = {}
        dg = config.downgrade
        if dg and dg.enabled and dg.date_source == "plex":
            plex_added_map, plex_4k_map, _ = await _build_plex_added_map(config)

        # ── Execute due downgrades first ──────────────────────────────────────
        just_executed_ids: set[int] = set()
        if dg and dg.enabled:
            just_executed_ids = await _execute_due_downgrades(radarr, profile_map, stats, config)
            # Refresh movie list so the rest of the sync sees current state
            # (stale data would re-queue just-downgraded movies on the same run)
            all_movies = await radarr.get_movies()
            tmdb_to_movie = {m["tmdbId"]: m for m in all_movies}

        # ── MDBList sync ──────────────────────────────────────────────────────
        active_lists = [l for l in config.lists if l.enabled]
        if active_lists:
            if not config.mdblist:
                logger.warning("MDBList lists configured but no API key — skipping.")
            else:
                mdblist = MDBListClient(config.mdblist.api_key)
                for list_cfg in active_lists:
                    await _sync_mdblist(
                        list_cfg, radarr, mdblist,
                        profile_map, profile_id_to_name, tmdb_to_movie,
                        default_root, dg, plex_added_map, stats
                    )

        # ── Plex Watchlist sync ───────────────────────────────────────────────
        if config.plex and config.plex.enabled:
            if not config.plex.token:
                logger.warning("[Plex] Enabled but no token configured — skipping.")
            elif not config.plex.sync_own and not config.plex.sync_friends:
                logger.warning("[Plex] Enabled but both sync_own and sync_friends are off — skipping.")
            else:
                try:
                    rss_urls = await fetch_plex_rss_urls(config.plex.token)
                    plex_rss = PlexRSSClient(
                        rss_own=rss_urls["rss_own"] if config.plex.sync_own else None,
                        rss_friends=rss_urls["rss_friends"] if config.plex.sync_friends else None,
                    )
                    await _sync_plex_watchlist(
                        config.plex, radarr, plex_rss,
                        profile_map, profile_id_to_name, tmdb_to_movie,
                        default_root, dg, plex_added_map, stats
                    )
                except Exception as e:
                    logger.error(f"[Plex] Failed to fetch RSS URLs from plex.tv: {e}")

        # ── Ombi Requests sync ────────────────────────────────────────────────
        if config.ombi and config.ombi.enabled:
            ombi = OmbiClient(config.ombi.url, config.ombi.api_key)
            await _sync_ombi(
                config.ombi, radarr, ombi,
                profile_map, profile_id_to_name, tmdb_to_movie,
                default_root, dg, plex_added_map, stats
            )

        # ── Global downgrade pass ─────────────────────────────────────────────
        if dg and dg.enabled:
            await _sync_downgrade(dg, radarr, profile_map, profile_id_to_name,
                                  tmdb_to_movie, plex_added_map, plex_4k_map, stats,
                                  just_executed_ids)

    except Exception as e:
        logger.error(f"Sync failed: {e}", exc_info=True)
        stats["errors"] += 1
    finally:
        _sync_running = False
        _last_sync = {"time": datetime.utcnow().isoformat(), "stats": stats}
        logger.info(f"=== Sync complete: {stats} ===")


# ── Global downgrade pass ─────────────────────────────────────────────────────

async def _sync_downgrade(dg: DowngradeConfig, radarr: RadarrClient,
                           profile_map: dict, profile_id_to_name: dict,
                           tmdb_to_movie: dict, plex_added_map: dict,
                           plex_4k_map: dict, stats: dict,
                           just_executed_ids: set[int] | None = None):
    logger.info(f"[Downgrade] Scanning library for movies older than {dg.older_than_days}d → '{dg.quality_profile}'")

    target_profile_id = profile_map.get(dg.quality_profile.lower())
    if not target_profile_id:
        logger.error(f"  [Downgrade] Profile '{dg.quality_profile}' not found in Radarr — skipping downgrade pass")
        return

    threshold = datetime.utcnow() - timedelta(days=dg.older_than_days)
    logger.info(f"[Downgrade] Threshold: movies added before {threshold.date()} qualify ({dg.older_than_days}d)")
    logger.info(f"[Downgrade] Date map: {len(plex_added_map)} Plex entries, {sum(v for v in plex_4k_map.values())} are 4K | Radarr library: {len(tmdb_to_movie)} movies")

    candidates = 0
    skip_not_4k = 0
    skip_no_file = 0
    skip_no_date = 0
    skip_too_new = 0
    qualifying_tmdb_ids: set[int] = set()

    for tmdb_id, movie in tmdb_to_movie.items():
        title = movie.get("title", f"TMDB:{tmdb_id}")

        # Primary filter: file must be physically 4K in Plex
        if plex_4k_map:
            if not plex_4k_map.get(tmdb_id, False):
                skip_not_4k += 1
                continue
        else:
            # Radarr date source — no Plex resolution data, skip if already at target profile
            if movie["qualityProfileId"] == target_profile_id:
                skip_not_4k += 1
                continue

        # Skip if no file tracked in Radarr
        movie_file = movie.get("movieFile", {})
        if not movie_file:
            logger.debug(f"  SKIP '{title}' — no file on disk in Radarr")
            skip_no_file += 1
            continue

        added = _get_added_date(movie, tmdb_id, plex_added_map)
        if added is None:
            logger.debug(f"  SKIP '{title}' — could not determine added date")
            skip_no_date += 1
            continue
        if added >= threshold:
            logger.debug(f"  SKIP '{title}' — too new ({added.date()} >= threshold {threshold.date()})")
            skip_too_new += 1
            continue

        # Movie still qualifies — track it so we can detect stale queue entries later
        qualifying_tmdb_ids.add(tmdb_id)

        # Skip re-queuing if this movie was just executed in this same sync run
        # (e.g. Tdarr method: file is still 4K on disk until the transcode completes)
        if just_executed_ids and tmdb_id in just_executed_ids:
            logger.debug(f"  SKIP re-queue '{title}' — just executed in this sync run")
            continue

        candidates += 1
        file_id = movie_file.get("id", 0) if isinstance(movie_file, dict) else 0
        current_profile = profile_id_to_name.get(movie["qualityProfileId"], str(movie["qualityProfileId"]))

        queued = await queue_downgrade(
            source_id="downgrade", source_name="Global Downgrade",
            movie_title=title, tmdb_id=tmdb_id,
            radarr_movie_id=movie["id"], radarr_file_id=file_id,
            current_profile=current_profile, target_profile=dg.quality_profile,
            grace_days=dg.grace_days,
            plex_added_at=added.isoformat() if added else "",
        )
        if queued:
            logger.info(f"  [Downgrade] QUEUED '{title}' (4K file, added {added.date()}) → '{dg.quality_profile}' in {dg.grace_days}d")
            stats["downgrade_queued"] += 1
            await add_history_entry("downgrade", "Global Downgrade", title, tmdb_id,
                                    "downgrade_queued",
                                    f"4K file → '{dg.quality_profile}' (grace: {dg.grace_days}d)")

    logger.info(f"[Downgrade] Scan complete — {candidates} candidate(s) found")
    logger.info(f"[Downgrade] Skipped: {skip_not_4k} not 4K in Plex, {skip_no_file} no file, {skip_no_date} no date, {skip_too_new} too new")

    # Cancel any pending entries whose movies no longer qualify (file deleted, removed from Plex, etc.)
    pending_entries = await get_pending_downgrades(status="pending")
    for entry in pending_entries:
        if entry.tmdb_id not in qualifying_tmdb_ids:
            await update_downgrade_status(entry.id, "cancelled")
            logger.info(f"  [Downgrade] CANCELLED stale entry for '{entry.movie_title}' — no longer qualifies (file gone or removed from Plex)")
            await add_history_entry("downgrade", "Global Downgrade", entry.movie_title, entry.tmdb_id,
                                    "downgrade_cancelled",
                                    "No longer qualifies — file deleted or removed from Plex")


# ── Threshold helpers ─────────────────────────────────────────────────────────

async def _build_plex_added_map(config) -> tuple[dict[int, datetime], dict[int, bool], dict[int, str]]:
    """
    Fetch local Plex library and build:
      - tmdb_id → addedAt (datetime)
      - tmdb_id → is_4k (bool)
      - tmdb_id → file path (str)  ← used by the Tdarr downgrade method
    Returns (date_map, is_4k_map, file_map). All empty dicts on failure.
    """
    plex_url = config.plex.url if config.plex else None
    plex_token = config.plex.token if config.plex else None

    if not plex_url or not plex_token:
        logger.warning("[Downgrade] date_source=plex but plex.url or plex.token not set in config")
        return {}, {}, {}

    try:
        import httpx
        date_map: dict[int, datetime] = {}
        is_4k_map: dict[int, bool] = {}
        file_map: dict[int, str] = {}

        async with httpx.AsyncClient(timeout=30, verify=False) as client:
            r = await client.get(f"{plex_url}/library/sections",
                                 params={"X-Plex-Token": plex_token},
                                 headers={"Accept": "application/json"})
            r.raise_for_status()
            sections = r.json().get("MediaContainer", {}).get("Directory", [])
            for section in [s for s in sections if s.get("type") == "movie"]:
                sid = section.get("key")
                r2 = await client.get(f"{plex_url}/library/sections/{sid}/all",
                                      params={"X-Plex-Token": plex_token, "type": 1, "includeGuids": 1},
                                      headers={"Accept": "application/json"})
                r2.raise_for_status()
                for item in r2.json().get("MediaContainer", {}).get("Metadata", []):
                    added_at = item.get("addedAt")
                    if not added_at:
                        continue
                    # Determine if any media part is 4K; also capture the first file path
                    resolution = ""
                    file_path = ""
                    for media in item.get("Media", []):
                        resolution = str(media.get("videoResolution", "")).lower()
                        if not file_path:
                            parts = media.get("Part", [])
                            if parts:
                                file_path = parts[0].get("file", "")
                        if resolution == "4k":
                            break
                    for g in item.get("Guid", []):
                        gid = g.get("id", "")
                        if gid.startswith("tmdb://"):
                            try:
                                tmdb_id = int(gid.replace("tmdb://", ""))
                                date_map[tmdb_id] = datetime.utcfromtimestamp(added_at)
                                is_4k_map[tmdb_id] = (resolution == "4k")
                                if file_path:
                                    file_map[tmdb_id] = file_path
                            except ValueError:
                                pass

        is_4k_count = sum(1 for v in is_4k_map.values() if v)
        logger.info(f"[Downgrade] Plex map: {len(date_map)} movies, {is_4k_count} are 4K, {len(file_map)} with file paths")
        if date_map:
            sample = [(tmdb_id, date_map[tmdb_id], is_4k_map[tmdb_id]) for tmdb_id in list(date_map)[:3]]
            for tmdb_id, dt, is_4k in sample:
                logger.info(f"  Sample — TMDB:{tmdb_id} added {dt.date()} 4K={is_4k}")
        else:
            logger.warning("[Downgrade] Plex map is empty — check library has movies with TMDB GUIDs")
        return date_map, is_4k_map, file_map
    except Exception as e:
        logger.warning(f"[Downgrade] Failed to build Plex map: {e}")
        return {}, {}, {}


def _get_added_date(movie: dict, tmdb_id: int, plex_added_map: dict) -> datetime | None:
    if plex_added_map is not None and len(plex_added_map) > 0:
        # Plex is the configured date source — use it exclusively
        plex_date = plex_added_map.get(tmdb_id)
        if plex_date is None:
            logger.debug(f"  TMDB:{tmdb_id} not found in Plex library date map — skipping")
        return plex_date

    # Radarr date source
    added_str = movie.get("added", "")
    if not added_str or added_str.startswith("0001"):
        return None
    try:
        return datetime.fromisoformat(added_str.replace("Z", "+00:00").replace("+00:00", ""))
    except ValueError:
        return None


def _is_upgrade_blocked(movie: dict, tmdb_id: int, dg, plex_added_map: dict) -> bool:
    """Return True if upgrade_threshold is active and this movie is too old to upgrade."""
    if not dg or not dg.enabled or not dg.upgrade_threshold:
        return False
    added = _get_added_date(movie, tmdb_id, plex_added_map)
    if added is None:
        return False  # Unknown date — allow upgrade
    threshold = datetime.utcnow() - timedelta(days=dg.older_than_days)
    return added < threshold


# ── Execute due downgrades ────────────────────────────────────────────────────

async def _execute_due_downgrades(radarr: RadarrClient, profile_map: dict, stats: dict,
                                   config=None) -> set[int]:
    due = await get_due_downgrades()
    if not due:
        return set()
    logger.info(f"[Downgrade] {len(due)} downgrade(s) due for execution")
    executed_ids: set[int] = set()

    method = "redownload"
    tdarr_cfg = None
    if config and config.downgrade:
        method = getattr(config.downgrade, "method", "redownload") or "redownload"
    if config and config.tdarr:
        tdarr_cfg = config.tdarr

    for d in due:
        try:
            movies = await radarr.get_movies()
            movie = next((m for m in movies if m["tmdbId"] == d.tmdb_id), None)
            if not movie:
                logger.warning(f"  [Downgrade] '{d.movie_title}' not found in Radarr — cancelling")
                await update_downgrade_status(d.id, "cancelled")
                continue

            target_id = profile_map.get(d.target_profile.lower())
            if target_id is None:
                logger.error(f"  [Downgrade] Profile '{d.target_profile}' not found — cancelling")
                await update_downgrade_status(d.id, "cancelled")
                continue

            movie["qualityProfileId"] = target_id
            has_file = bool(movie.get("movieFile"))

            if method == "tdarr" and tdarr_cfg:
                # ── Tdarr method: re-encode in place, keep the file ──────────
                await radarr.update_movie(movie)

                # Use Radarr's own file path — no need for Plex file map
                radarr_path = (movie.get("movieFile") or {}).get("path", "")
                tdarr_path = radarr_path
                if radarr_path and tdarr_cfg.path_replace_from and tdarr_cfg.path_replace_to:
                    # Strip trailing slashes so "/from/" and "/from" both work correctly
                    _from = tdarr_cfg.path_replace_from.rstrip("/")
                    _to = tdarr_cfg.path_replace_to.rstrip("/")
                    tdarr_path = radarr_path.replace(_from, _to, 1)

                if tdarr_path and has_file:
                    tdarr_client = TdarrClient(tdarr_cfg.url, tdarr_cfg.library_id)
                    await tdarr_client.send_file(tdarr_path)
                    logger.info(f"  [Downgrade] EXECUTED '{d.movie_title}' — sent to Tdarr for re-encode → '{d.target_profile}' (path: {tdarr_path})")
                    note = f"'{d.current_profile}' → '{d.target_profile}' via Tdarr re-encode"
                else:
                    logger.warning(f"  [Downgrade] '{d.movie_title}' — Tdarr method but no file path found; profile updated only")
                    note = f"'{d.current_profile}' → '{d.target_profile}' (profile only, no Tdarr path)"

            else:
                # ── Redownload method: delete file and re-grab ───────────────
                if has_file and d.radarr_file_id and d.radarr_file_id > 0:
                    await radarr.update_movie(movie)
                    await radarr.delete_movie_file(d.radarr_file_id)
                    # Re-monitor and search — Radarr's "unmonitor on delete" setting would
                    # otherwise leave the movie unmonitored with no re-download triggered.
                    movie["monitored"] = True
                    await radarr.update_movie(movie)
                    await radarr.search_movie(movie["id"])
                    logger.info(f"  [Downgrade] EXECUTED '{d.movie_title}' — file deleted, re-monitored, search triggered → '{d.target_profile}'")
                    note = f"'{d.current_profile}' → '{d.target_profile}' + file deleted"
                else:
                    # File already gone (deleted externally or Plex removed it) —
                    # just update the profile, re-monitor, and search.
                    movie["monitored"] = True
                    await radarr.update_movie(movie)
                    await radarr.search_movie(movie["id"])
                    logger.info(f"  [Downgrade] EXECUTED '{d.movie_title}' — no file on disk, re-monitored, search triggered → '{d.target_profile}'")
                    note = f"'{d.current_profile}' → '{d.target_profile}' (no file, search triggered)"

            await update_downgrade_status(d.id, "executed")
            executed_ids.add(d.tmdb_id)
            stats["downgrade_executed"] += 1
            await add_history_entry(d.source_id, d.source_name, d.movie_title, d.tmdb_id,
                                    "downgraded", note)
        except Exception as e:
            logger.error(f"  [Downgrade] Failed for '{d.movie_title}': {e}")
            stats["errors"] += 1

    return executed_ids


# ── MDBList ───────────────────────────────────────────────────────────────────

async def _sync_mdblist(list_cfg, radarr, mdblist, profile_map, profile_id_to_name,
                         tmdb_to_movie, default_root, dg, plex_added_map, stats):
    list_name = list_cfg.list_name or list_cfg.list_id
    logger.info(f"[MDBList] Processing list: {list_name} → profile '{list_cfg.quality_profile}'")

    target_profile_id = profile_map.get(list_cfg.quality_profile.lower())
    if target_profile_id is None:
        logger.error(f"  Profile '{list_cfg.quality_profile}' not found in Radarr.")
        return

    try:
        items = await mdblist.get_list_items(list_cfg.list_id)
    except Exception as e:
        logger.error(f"  Failed to fetch list {list_cfg.list_id}: {e}")
        return

    for item in items:
        tmdb_id = mdblist.extract_tmdb_id(item)
        title = mdblist.extract_title(item)
        if not tmdb_id:
            continue
        await _apply_profile(
            radarr=radarr, tmdb_id=tmdb_id, title=title,
            target_profile_id=target_profile_id, profile_name=list_cfg.quality_profile,
            profile_id_to_name=profile_id_to_name, tmdb_to_movie=tmdb_to_movie,
            add_missing=list_cfg.add_missing, search_on_update=list_cfg.search_on_update,
            root_folder=list_cfg.root_folder or default_root,
            monitored=list_cfg.monitored, minimum_availability=list_cfg.minimum_availability,
            search_on_add=list_cfg.search_on_add,
            source_id=list_cfg.list_id, source_name=list_name,
            dg=dg, plex_added_map=plex_added_map, stats=stats,
        )


# ── Plex ──────────────────────────────────────────────────────────────────────

async def _sync_plex_watchlist(plex_cfg, radarr, plex_rss, profile_map, profile_id_to_name,
                                tmdb_to_movie, default_root, dg, plex_added_map, stats):
    logger.info(f"[Plex] Processing watchlist → profile '{plex_cfg.quality_profile}'")

    target_profile_id = profile_map.get(plex_cfg.quality_profile.lower())
    if target_profile_id is None:
        logger.error(f"  Profile '{plex_cfg.quality_profile}' not found in Radarr.")
        return

    try:
        items = await plex_rss.get_watchlist()
    except Exception as e:
        logger.error(f"  Failed to fetch Plex watchlist: {e}", exc_info=True)
        return

    for item in items:
        imdb_id = item.get("imdb_id")
        title = item.get("title", "Unknown")
        if not imdb_id:
            continue

        tmdb_id = _imdb_to_tmdb(imdb_id, tmdb_to_movie)
        if not tmdb_id:
            try:
                result = await radarr.lookup_by_imdb(imdb_id)
                if result:
                    tmdb_id = result.get("tmdbId")
                    title = result.get("title", title)
            except Exception as e:
                logger.warning(f"  Radarr lookup failed for {imdb_id}: {e}")

        if not tmdb_id:
            logger.warning(f"  Could not resolve TMDB ID for '{title}' ({imdb_id}) — skipping")
            continue

        await _apply_profile(
            radarr=radarr, tmdb_id=tmdb_id, title=title,
            target_profile_id=target_profile_id, profile_name=plex_cfg.quality_profile,
            profile_id_to_name=profile_id_to_name, tmdb_to_movie=tmdb_to_movie,
            add_missing=plex_cfg.add_missing, search_on_update=plex_cfg.search_on_update,
            root_folder=plex_cfg.root_folder or default_root,
            monitored=plex_cfg.monitored, minimum_availability=plex_cfg.minimum_availability,
            search_on_add=plex_cfg.search_on_add,
            source_id="plex_watchlist", source_name="Plex Watchlist",
            dg=dg, plex_added_map=plex_added_map, stats=stats,
        )


def _imdb_to_tmdb(imdb_id: str, tmdb_to_movie: dict) -> int | None:
    for movie in tmdb_to_movie.values():
        if movie.get("imdbId") == imdb_id:
            return movie.get("tmdbId")
    return None


# ── Ombi ──────────────────────────────────────────────────────────────────────

async def _sync_ombi(ombi_cfg, radarr, ombi, profile_map, profile_id_to_name,
                      tmdb_to_movie, default_root, dg, plex_added_map, stats):
    mode = "approved only" if ombi_cfg.approved_only else "all non-denied"
    logger.info(f"[Ombi] Processing requests ({mode}) → profile '{ombi_cfg.quality_profile}'")

    target_profile_id = profile_map.get(ombi_cfg.quality_profile.lower())
    if target_profile_id is None:
        logger.error(f"  Profile '{ombi_cfg.quality_profile}' not found in Radarr.")
        return

    try:
        items = await ombi.get_movie_requests(approved_only=ombi_cfg.approved_only)
    except Exception as e:
        logger.error(f"  Failed to fetch Ombi requests: {e}", exc_info=True)
        return

    for item in items:
        await _apply_profile(
            radarr=radarr, tmdb_id=item["tmdb_id"], title=item["title"],
            target_profile_id=target_profile_id, profile_name=ombi_cfg.quality_profile,
            profile_id_to_name=profile_id_to_name, tmdb_to_movie=tmdb_to_movie,
            add_missing=ombi_cfg.add_missing, search_on_update=ombi_cfg.search_on_update,
            root_folder=ombi_cfg.root_folder or default_root,
            monitored=ombi_cfg.monitored, minimum_availability=ombi_cfg.minimum_availability,
            search_on_add=ombi_cfg.search_on_add,
            source_id="ombi", source_name="Ombi Requests",
            dg=dg, plex_added_map=plex_added_map, stats=stats,
        )


# ── Shared core ───────────────────────────────────────────────────────────────

async def _apply_profile(
    radarr, tmdb_id, title, target_profile_id, profile_name,
    profile_id_to_name, tmdb_to_movie, add_missing, search_on_update,
    root_folder, monitored, minimum_availability, search_on_add,
    source_id, source_name, dg, plex_added_map, stats,
):
    try:
        if tmdb_id in tmdb_to_movie:
            movie = tmdb_to_movie[tmdb_id]

            if movie["qualityProfileId"] == target_profile_id:
                logger.debug(f"  SKIP '{title}' — already correct profile")
                stats["skipped"] += 1
                await add_history_entry(source_id, source_name, title, tmdb_id,
                                        "skipped", "Already correct profile")
                return

            # Block upgrade if movie is old and upgrade_threshold is active
            if _is_upgrade_blocked(movie, tmdb_id, dg, plex_added_map):
                logger.debug(f"  SKIP '{title}' — upgrade blocked by downgrade threshold")
                stats["skipped"] += 1
                return

            old_profile_id = movie["qualityProfileId"]
            movie["qualityProfileId"] = target_profile_id
            await radarr.update_movie(movie)
            logger.info(f"  UPDATED '{title}' (profile {old_profile_id} → {target_profile_id})")
            if search_on_update:
                await radarr.search_movie(movie["id"])
            stats["updated"] += 1
            await add_history_entry(source_id, source_name, title, tmdb_id, "updated",
                                    f"Profile → '{profile_name}'" + (" + search" if search_on_update else ""))

        elif add_missing:
            await radarr.add_movie(
                tmdb_id=tmdb_id, quality_profile_id=target_profile_id,
                root_folder=root_folder, monitored=monitored,
                minimum_availability=minimum_availability, search_on_add=search_on_add,
            )
            logger.info(f"  ADDED '{title}'")
            stats["added"] += 1
            await add_history_entry(source_id, source_name, title, tmdb_id, "added",
                                    f"Added with profile '{profile_name}'")
        else:
            logger.debug(f"  NOT IN RADARR '{title}' — add_missing=false")
            stats["skipped"] += 1

    except Exception as e:
        logger.error(f"  ERROR processing '{title}': {e}")
        stats["errors"] += 1
        await add_history_entry(source_id, source_name, title, tmdb_id, "error", str(e))
