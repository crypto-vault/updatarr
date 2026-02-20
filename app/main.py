import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import httpx
import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .config import load_config
from .database import init_db, get_history, add_history_entry
from .sync import run_sync

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("updatarr")

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    config = load_config()
    if config.schedule:
        scheduler.add_job(
            run_sync,
            CronTrigger.from_crontab(config.schedule),
            id="sync_job",
            replace_existing=True,
        )
        logger.info(f"Scheduled sync: {config.schedule}")
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="Updatarr", lifespan=lifespan)

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    config = load_config()
    history = await get_history(limit=50)
    return templates.TemplateResponse("index.html", {
        "request": request,
        "config": config,
        "history": history,
    })


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
    config = load_config()
    return JSONResponse({
        "radarr_url": config.radarr.url,
        "lists_count": len(config.lists),
        "schedule": config.schedule,
        "next_run": str(scheduler.get_job("sync_job").next_run_time) if scheduler.get_job("sync_job") else None,
    })
