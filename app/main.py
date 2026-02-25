import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import io

import httpx
import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image
from pydantic import BaseModel

from .config import load_config, save_config
from .database import init_db, get_history, get_pending_downgrades, \
    get_exclusions, exclude_downgrade, restore_downgrade, update_downgrade_status
from .sync import run_sync

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("updatarr")

scheduler = AsyncIOScheduler()


def _reschedule(cron: str | None):
    scheduler.remove_job("sync_job") if scheduler.get_job("sync_job") else None
    if cron:
        scheduler.add_job(
            run_sync,
            CronTrigger.from_crontab(cron),
            id="sync_job",
            replace_existing=True,
        )
        logger.info(f"Rescheduled sync: {cron}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    try:
        config = load_config()
        _reschedule(config.schedule)
    except Exception as e:
        logger.warning(f"Could not load config on startup: {e}")
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="Updatarr", lifespan=lifespan)

BASE_DIR = Path(__file__).parent

# Poster cache — persists alongside the database
POSTER_DIR = Path("/config/posters") if Path("/config").exists() else Path("posters")
POSTER_DIR.mkdir(parents=True, exist_ok=True)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.mount("/posters", StaticFiles(directory=str(POSTER_DIR)), name="posters")


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    config = load_config()
    history = await get_history(limit=50)
    return templates.TemplateResponse("index.html", {
        "request": request,
        "config": config,
        "history": history,
        "active": "dashboard",
    })


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    config = load_config()
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "config": config,
        "active": "settings",
    })


@app.get("/queue", response_class=HTMLResponse)
async def queue_page(request: Request):
    return templates.TemplateResponse("queue.html", {"request": request})


@app.get("/api/tmdb-poster/{tmdb_id}")
async def tmdb_poster(tmdb_id: int):
    """Ensure poster is cached locally, then redirect to the static /posters/ URL.
    The browser caches the static file (ETag + 304) so subsequent loads are free."""
    poster_path = POSTER_DIR / f"{tmdb_id}.jpg"

    # Cache hit — redirect straight to the static file (301 so browser caches the redirect too)
    if poster_path.exists():
        return RedirectResponse(f"/posters/{tmdb_id}.jpg", status_code=301)

    # Cache miss — ask Radarr for the remote URL, fetch and resize, then redirect
    try:
        config = load_config()
        async with httpx.AsyncClient(timeout=10, verify=False) as client:
            # Step 1: get the remote poster URL from Radarr's lookup
            r = await client.get(
                f"{config.radarr.url.rstrip('/')}/api/v3/movie/lookup/tmdb",
                params={"tmdbId": tmdb_id},
                headers={"X-Api-Key": config.radarr.api_key},
            )
            if r.status_code != 200:
                return JSONResponse({"poster_path": None})

            data = r.json()
            images = data.get("images", []) if isinstance(data, dict) else []
            remote_url = None
            for img in images:
                if img.get("coverType") == "poster":
                    remote_url = img.get("remoteUrl") or img.get("url")
                    break

            if not remote_url:
                return JSONResponse({"poster_path": None})

            # Step 2: fetch, resize to max 500px wide, and save
            img_r = await client.get(remote_url, follow_redirects=True)
            if img_r.status_code == 200 and img_r.content:
                try:
                    img = Image.open(io.BytesIO(img_r.content))
                    img.thumbnail((500, 1500), Image.LANCZOS)
                    img = img.convert("RGB")
                    img.save(poster_path, "JPEG", quality=85, optimize=True)
                except Exception:
                    poster_path.write_bytes(img_r.content)  # fallback: save raw
                return RedirectResponse(f"/posters/{tmdb_id}.jpg", status_code=301)

    except Exception:
        pass

    return JSONResponse({"poster_path": None})


# ── Sync API ──────────────────────────────────────────────────────────────────

@app.post("/api/sync")
async def trigger_sync():
    asyncio.create_task(run_sync())
    return JSONResponse({"status": "started", "message": "Sync triggered"})


@app.get("/api/history")
async def api_history():
    history = await get_history(limit=100)
    return JSONResponse([h.__dict__ for h in history])


@app.get("/api/status")
async def api_status():
    from .sync import get_sync_status
    config = load_config()
    sync = get_sync_status()
    return JSONResponse({
        "radarr_url": config.radarr.url,
        "lists_count": len(config.lists),
        "schedule": config.schedule,
        "next_run": str(scheduler.get_job("sync_job").next_run_time) if scheduler.get_job("sync_job") else None,
        "sync_running": sync["running"],
        "last_sync": sync["last"],
    })


# ── Settings API ──────────────────────────────────────────────────────────────

@app.get("/api/config")
async def api_get_config():
    config = load_config()
    return JSONResponse(config.model_dump())


@app.post("/api/config")
async def api_save_config(request: Request):
    try:
        data = await request.json()
        # Validate by constructing the model
        from .config import AppConfig
        AppConfig(**data)
        save_config(data)
        # Reschedule if cron changed
        _reschedule(data.get("schedule"))
        return JSONResponse({"status": "ok", "message": "Configuration saved"})
    except Exception as e:
        logger.error(f"Config save failed: {e}", exc_info=True)
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)


@app.get("/api/pending-downgrades")
async def api_pending_downgrades():
    pending = await get_pending_downgrades(status="pending")
    return JSONResponse([p.__dict__ for p in pending])


@app.post("/api/pending-downgrades/{downgrade_id}/cancel")
async def api_cancel_downgrade(downgrade_id: int):
    await update_downgrade_status(downgrade_id, "cancelled")
    return JSONResponse({"status": "ok"})


@app.post("/api/pending-downgrades/{downgrade_id}/exclude")
async def api_exclude_downgrade(downgrade_id: int):
    await exclude_downgrade(downgrade_id)
    return JSONResponse({"status": "ok"})


@app.get("/api/exclusions")
async def api_get_exclusions():
    exclusions = await get_exclusions()
    return JSONResponse([e.__dict__ for e in exclusions])


@app.post("/api/exclusions/{downgrade_id}/restore")
async def api_restore_downgrade(downgrade_id: int):
    await restore_downgrade(downgrade_id)
    return JSONResponse({"status": "ok"})


@app.post("/api/test/radarr")
async def test_radarr(request: Request):
    data = await request.json()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{data['url'].rstrip('/')}/api/v3/system/status",
                headers={"X-Api-Key": data["api_key"]},
            )
            if r.status_code == 200:
                info = r.json()
                return JSONResponse({"status": "ok", "message": f"Connected — Radarr v{info.get('version', '?')}"})
            return JSONResponse({"status": "error", "message": f"HTTP {r.status_code}"}, status_code=400)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)


@app.post("/api/test/mdblist")
async def test_mdblist(request: Request):
    data = await request.json()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://mdblist.com/api/",
                params={"apikey": data["api_key"], "s": "test"},
            )
            if r.status_code == 200:
                return JSONResponse({"status": "ok", "message": "MDBList API key valid"})
            return JSONResponse({"status": "error", "message": f"HTTP {r.status_code}"}, status_code=400)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)


@app.post("/api/test/ombi")
async def test_ombi(request: Request):
    data = await request.json()
    try:
        from .ombi import OmbiClient
        client = OmbiClient(data["url"], data["api_key"])
        ok, msg = await client.validate()
        if ok:
            return JSONResponse({"status": "ok", "message": "Connected to Ombi"})
        return JSONResponse({"status": "error", "message": msg}, status_code=400)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)



@app.post("/api/test/plex")
async def test_plex(request: Request):
    data = await request.json()
    token = data.get("token")
    url = data.get("url")
    if not token:
        return JSONResponse({"status": "error", "message": "No Plex token provided"}, status_code=400)
    try:
        from .plex import fetch_plex_rss_urls
        rss = await fetch_plex_rss_urls(token)
        msg = "Connected — RSS URLs resolved"
        # Try to get server name from local URL (best-effort, don't fail if it errors)
        if url:
            try:
                async with httpx.AsyncClient(timeout=10, verify=False) as client:
                    r = await client.get(
                        f"{url.rstrip('/')}/",
                        params={"X-Plex-Token": token},
                        headers={"Accept": "application/json"},
                    )
                    r.raise_for_status()
                    name = r.json().get("MediaContainer", {}).get("friendlyName", "")
                    if name:
                        msg = f"Connected to '{name}' — RSS URLs resolved"
            except Exception:
                pass  # Local URL check is optional — don't block the response
        return JSONResponse({"status": "ok", "message": msg, "rss_own": rss["rss_own"], "rss_friends": rss["rss_friends"]})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)


@app.post("/api/test/tdarr")
async def test_tdarr(request: Request):
    data = await request.json()
    try:
        from .tdarr import TdarrClient
        client = TdarrClient(data["url"], data["library_id"])
        ok, msg = await client.validate()
        if ok:
            return JSONResponse({"status": "ok", "message": msg})
        return JSONResponse({"status": "error", "message": msg}, status_code=400)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)
