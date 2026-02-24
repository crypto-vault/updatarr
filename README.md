# Updatarr

**Automatically manages Radarr quality profiles based on your watchlists, curated lists, and media requests.**

Updatarr bridges the gap between *wanting* a movie in 4K and *having* it in 4K. It monitors external sources — MDBList lists, your Plex watchlist, and Ombi requests — and whenever a movie from those sources appears in your Radarr library, it automatically upgrades the quality profile to whatever you've configured for that source. No manual intervention, no forgetting to update profiles after adding a movie.

### How it works

On a schedule (or triggered manually), Updatarr fetches movies from your configured sources and compares them against your Radarr library. For each match, it sets the assigned quality profile on that movie, prompting Radarr to search for a better version if one isn't already available. It can also add movies that are missing from Radarr entirely.

**Supported sources:**

- **MDBList lists** — any public or private list on mdblist.com, each mappable to a different quality profile. Useful for curated 4K collections, award winners, director filmographies, or any list-based workflow.
- **Plex watchlist** — movies you (and optionally your friends) have added to their Plex watchlist are automatically upgraded when they land in your library.
- **Ombi requests** — movies requested through Ombi are upgraded when they appear in Radarr, ensuring requested content is grabbed at the right quality.

### Downgrade queue

Updatarr also works in reverse. The optional **global downgrade** feature identifies movies in your library that are currently stored in 4K (verified against your actual Plex library files, not just the Radarr profile) and have been in your collection long enough to no longer be a priority — based on how long ago they were added to Plex and a configurable age threshold. Qualifying movies are moved to a **downgrade queue** with a grace period before execution, giving you time to review and intervene.

The queue is fully visual — poster cards with countdown timers showing days until each downgrade executes. Movies can be excluded permanently (they'll never be re-queued on future syncs) or restored to the queue at any time. The exclusion list is managed from the same page.

---

## Quick Start

### Docker Compose (recommended)

```bash
# 1. Clone / download this repo
# 2. Create your config directory and config file
mkdir config
cp updatarr.example.yml config/updatarr.yml
# Edit config/updatarr.yml with your API keys and list mappings

# 3. Start
docker compose up -d

# 4. Open the UI
open http://localhost:7777
```

### Local / Development

```bash
pip install -r requirements.txt
cp updatarr.example.yml updatarr.yml
# Edit updatarr.yml

uvicorn app.main:app --reload --port 7777
```

---

## Configuration

Edit `config/updatarr.yml` (inside the container volume):

```yaml
radarr:
  url: http://radarr:7878
  api_key: YOUR_RADARR_API_KEY

mdblist:
  api_key: YOUR_MDBLIST_API_KEY

schedule: "0 4 * * *"   # Daily at 4am, or null to disable

lists:
  - list_id: "123456"
    list_name: "4K Collection"
    quality_profile: "Ultra-HD"
    add_missing: false

  - list_id: "654321"
    list_name: "HD Watchlist"
    quality_profile: "HD - 720p/1080p"
    add_missing: true
    search_on_add: true
    root_folder: /movies
```

### List options

| Option | Default | Description |
|---|---|---|
| `list_id` | required | MDBList list ID (from list URL) |
| `list_name` | list_id | Friendly name for UI/logs |
| `quality_profile` | required | Exact Radarr quality profile name |
| `add_missing` | `false` | Add movies not in Radarr |
| `monitored` | `true` | Monitor when adding |
| `search_on_add` | `false` | Trigger search when adding |
| `root_folder` | first root | Root folder path for new movies |
| `minimum_availability` | `released` | `released` / `announced` / `inCinemas` |

---

## How to find your MDBList list ID

Go to your list on mdblist.com. The ID is in the URL:
`https://mdblist.com/lists/username/my-list/` → the numeric ID is visible in the API URL.

You can also use the MDBList API directly:
`https://mdblist.com/api/lists/mine?apikey=YOUR_KEY`

---

## Ports

| Port | Service |
|---|---|
| `7777` | Web UI + API |

---

## API

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Web UI |
| `/api/sync` | POST | Trigger manual sync |
| `/api/history` | GET | Sync history (JSON) |
| `/api/status` | GET | App status + next scheduled run |
