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

![Downgrade Queue](docs/queue-preview.png)

**Downgrade methods:**

- **Redownload** (default) — deletes the existing file and triggers Radarr to search for a new copy at the target quality profile.
- **Tdarr re-encode** — instead of deleting the file, submits it to a [Tdarr](https://home.tdarr.io/) library for in-place re-encoding. The file stays in your collection at all times; Tdarr transcodes it according to your configured flow (e.g. AV1 at a lower bitrate). Radarr's quality profile is updated to reflect the new target. Requires a Tdarr instance with a dedicated library configured for the downscale workflow.

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

Updatarr has a built-in **Settings page** (accessible at `http://localhost:7777/settings`) where you can configure all sources, schedules, and downgrade rules through a UI — no need to edit any files directly. The `updatarr.yml` file is the underlying config store and is documented here for reference, but you should rarely need to touch it manually.

The config file lives at `/config/updatarr.yml` inside the container (or `./updatarr.yml` for local dev). A full example with all options:

```yaml
radarr:
  url: http://radarr:7878         # Your Radarr URL
  api_key: YOUR_RADARR_API_KEY    # Settings > General > API Key

# ── MDBList (optional) ────────────────────────────────────────────────────────
mdblist:
  api_key: YOUR_MDBLIST_API_KEY   # mdblist.com > Account > API

# ── Plex Watchlist (optional) ─────────────────────────────────────────────────
plex:
  enabled: true
  url: http://plex:32400          # Local Plex server URL
  token: YOUR_PLEX_TOKEN          # Settings › Troubleshooting › Show Token
  sync_own: true                  # Sync your own watchlist
  sync_friends: false             # Also sync friends' watchlists
  quality_profile: "Ultra-HD"
  add_missing: false
  search_on_update: false         # Trigger Radarr search when profile is updated
  monitored: true
  search_on_add: false
  # root_folder: /movies
  # minimum_availability: released

# ── Ombi (optional) ───────────────────────────────────────────────────────────
ombi:
  enabled: true
  url: http://ombi:3579
  api_key: YOUR_OMBI_API_KEY      # Ombi: Settings > Configuration > API Key
  quality_profile: "Ultra-HD"
  approved_only: true             # Only sync admin-approved requests (recommended)
  add_missing: false
  search_on_update: false
  monitored: true
  search_on_add: false
  # root_folder: /movies
  # minimum_availability: released

# ── Downgrade (optional) ──────────────────────────────────────────────────────
# downgrade:
#   enabled: false
#   quality_profile: "HD-1080p"   # Target profile for downgraded movies
#   older_than_days: 730          # Qualify movies added more than N days ago
#   grace_days: 7                 # Days to wait before executing the downgrade
#   date_source: plex             # "radarr" (date added to Radarr) or "plex" (date added to Plex)
#   upgrade_threshold: true       # Prevent sync sources from re-upgrading old movies
#   method: redownload            # "redownload" (default) or "tdarr"

# ── Tdarr re-encode (optional, used when downgrade.method = "tdarr") ──────────
# tdarr:
#   url: http://tdarr:8265        # Tdarr server URL
#   library_id: "CcX2K_hrh"      # Library ID from Tdarr (not the UI integer — see below)
#   path_replace_from: /movies    # Radarr-side path prefix to replace (optional)
#   path_replace_to: /mnt/media/downscale/movies  # Tdarr-side path prefix

# Cron schedule for automatic sync (null to disable, manual only)
schedule: "0 4 * * *"            # Daily at 4am

# ── MDBList list mappings ─────────────────────────────────────────────────────
lists:
  - list_id: "123456"
    list_name: "4K Must-Watch"
    quality_profile: "Ultra-HD"
    enabled: true
    add_missing: false
    search_on_update: false

  - list_id: "654321"
    list_name: "HD Watchlist"
    quality_profile: "HD - 720p/1080p"
    enabled: true
    add_missing: true
    monitored: true
    search_on_add: true
    search_on_update: false
    root_folder: /movies
    minimum_availability: released
```

### MDBList list options

| Option | Default | Description |
|---|---|---|
| `list_id` | required | MDBList list ID (from list URL) |
| `list_name` | list_id | Friendly name shown in UI and logs |
| `quality_profile` | required | Exact Radarr quality profile name |
| `enabled` | `true` | Enable or disable this list without removing it |
| `add_missing` | `false` | Add movies not yet in Radarr |
| `monitored` | `true` | Monitor added movies |
| `search_on_add` | `false` | Trigger search immediately when adding |
| `search_on_update` | `false` | Trigger search when profile is updated |
| `root_folder` | first root | Root folder path for newly added movies |
| `minimum_availability` | `released` | `released` / `announced` / `inCinemas` |

### Tdarr re-encode options

| Option | Default | Description |
|---|---|---|
| `url` | required | Tdarr server URL (e.g. `http://tdarr:8265`) |
| `library_id` | required | Internal Tdarr library ID — **not** the integer shown in the UI. Use the Test Connection button in Settings to discover valid IDs. |
| `path_replace_from` | — | Path prefix as Radarr sees it (e.g. `/movies`) |
| `path_replace_to` | — | Equivalent path prefix as Tdarr sees it (e.g. `/mnt/media/downscale/movies`) |

Path replacement is needed when Radarr and Tdarr mount the same files under different paths inside their containers. Both values have trailing slashes stripped automatically so either form works.

The target Tdarr library must have **Process Library** enabled and a flow or plugin stack configured for downscaling. Folder watching does not need to be enabled — Updatarr submits files directly via the Tdarr API.

---

## How to find your MDBList list ID

Go to your list on mdblist.com. The ID is in the URL:
`https://mdblist.com/lists/username/my-list/` → the numeric ID is visible in the API URL.

You can also retrieve all your lists via the API:
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
| `/api/test/tdarr` | POST | Test Tdarr connection and verify library ID |
