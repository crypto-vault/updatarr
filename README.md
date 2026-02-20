# Updatarr

**Forces Radarr quality profiles based on MDBList lists.**

Updatarr runs on a schedule (or on-demand), fetches your configured MDBList lists, and ensures every matching movie in Radarr has the quality profile you've assigned to that list. Optionally adds missing movies.

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
