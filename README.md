# Updatarr

**Automatically manages Radarr quality profiles based on your watchlists, curated lists, and media requests.**

Updatarr bridges the gap between *wanting* a movie in 4K and *having* it in 4K. It monitors external sources — MDBList lists, your Plex watchlist, and Ombi requests — and whenever a movie from those sources appears in your Radarr library, it automatically upgrades the quality profile to whatever you've configured for that source. No manual intervention, no forgetting to update profiles after adding a movie.

### How it works

On a schedule (or triggered manually), Updatarr fetches movies from your configured sources and compares them against your Radarr library. For each match, it sets the assigned quality profile on that movie, prompting Radarr to search for a better version if one isn't already available. It can also add movies that are missing from Radarr entirely.

**Supported sources:**

- **MDBList lists** — any public or private list on mdblist.com, each mappable to a different quality profile. Useful for curated 4K collections, award winners, director filmographies, or any list-based workflow.
- **Plex watchlist** — movies you (and optionally your friends) have added to their Plex watchlist are automatically upgraded when they land in your library.
- **Ombi requests** — movies requested through Ombi are upgraded when they appear in Radarr, ensuring requested content is grabbed at the right quality.

### Retirement queue

Updatarr also works in reverse. The optional **retirement queue** identifies movies in your library that have been in your collection long enough that they're no longer a priority — based on how long ago they were added (to Plex or Radarr) and a configurable age threshold. Qualifying movies are placed into a **retirement queue** with a grace period before execution, giving you time to review and intervene.

The queue is fully visual — poster cards with countdown timers and a coloured action badge showing exactly what will happen to each movie. Movies can be excluded permanently (they'll never be re-queued on future syncs) or restored to the queue at any time.

![Retirement Queue](docs/queue-preview.png)

#### Multi-stage pipeline

Rather than a single retirement method, Updatarr supports an **ordered pipeline of stages**. Each stage has its own age threshold and action, so you can chain multiple operations over time:

> *Archive movies after 2 years → permanently delete them after 5 years*

Or more elaborate pipelines mixing any of the four actions. Stages are independent — each fires once per movie based on its own threshold, and completing one stage doesn't prevent later stages from triggering. You can configure any combination, in any order, with none required.

**Stage actions:**

- **Redownload** — updates the quality profile in Radarr, deletes the existing file, and triggers a new search at the target profile. Use this to downgrade a stored 4K copy to 1080p once a movie is older than its priority window.
- **Reencode** — submits the existing file to a [Tdarr](https://home.tdarr.io/) library for in-place re-encoding (e.g. AV1 at a lower bitrate). The file stays in your collection at all times and Radarr's profile is updated. Requires Tdarr configured below.
- **Archive** — moves the entire movie folder (file + subtitles + sidecar files) to a separate archive directory and unmonitors the movie in Radarr. No re-encode, no re-download — the file is preserved exactly as-is. The archive directory can be added as its own Plex library so the movie stays watchable.
- **Delete** — permanently removes the movie from Plex. Updatarr gets the current file location directly from Plex (so this works whether the file is still in the main library or has been previously archived), deletes the folder from disk, and unmonitors the movie in Radarr. The Radarr entry is kept so the movie won't be automatically re-requested.

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

Updatarr has a built-in **Settings page** (accessible at `http://localhost:7777/settings`) where you can configure all sources, schedules, and retirement stages through a UI — no need to edit any files directly. The `updatarr.yml` file is the underlying config store and is documented here for reference, but you should rarely need to touch it manually.

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

# ── Retirement queue (optional) ───────────────────────────────────────────────
# Processes old movies through an ordered pipeline of stages.
# Each stage has its own age threshold and action; all are optional.
# downgrade:
#   enabled: false
#   date_source: plex             # "plex" (added to Plex) or "radarr" (added to Radarr)
#   upgrade_threshold: true       # Prevent sync sources from re-upgrading retired movies
#   stages:
#     - action: redownload        # Downgrade quality profile and re-grab
#       older_than_days: 730
#       grace_days: 7
#       quality_profile: "HD-1080p"
#
#     - action: reencode          # Re-encode in place via Tdarr (requires tdarr: section)
#       older_than_days: 1095
#       grace_days: 14
#
#     - action: archive           # Move folder to archive dir (requires archive: section)
#       older_than_days: 1825
#       grace_days: 30
#
#     - action: delete            # Permanently remove from Plex, unmonitor in Radarr
#       older_than_days: 3650
#       grace_days: 30

# ── Tdarr re-encode (optional, used by the "reencode" retirement stage) ────────
# tdarr:
#   url: http://tdarr:8265        # Tdarr server URL
#   library_id: "aBcDeFgHi"      # Library ID from Tdarr (not the UI integer — see below)
#   path_replace_from: /movies    # Radarr-side path prefix to replace (optional)
#   path_replace_to: /mnt/media/downscale/movies  # Tdarr-side path prefix

# ── Archive (optional, used by the "archive" retirement stage) ────────────────
# archive:
#   path: /archive/movies          # destination as Updatarr sees it
#   path_replace_from: /movies     # Radarr-side path prefix (optional)
#   path_replace_to: /mnt/media/movies  # Updatarr-accessible source path prefix

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

---

## Option reference

### Radarr

| Option | Default | Description |
|---|---|---|
| `url` | required | Radarr server URL (e.g. `http://radarr:7878`) |
| `api_key` | required | API key from Radarr › Settings › General |

### MDBList

| Option | Default | Description |
|---|---|---|
| `api_key` | required | API key from mdblist.com › Account › API |

### MDBList list options

Each entry under `lists:` supports:

| Option | Default | Description |
|---|---|---|
| `list_id` | required | MDBList list ID (from list URL — see below) |
| `list_name` | list_id | Friendly name shown in UI and logs |
| `quality_profile` | required | Exact Radarr quality profile name |
| `enabled` | `true` | Enable or disable this list without removing it |
| `add_missing` | `false` | Add movies not yet in Radarr |
| `monitored` | `true` | Monitor added movies |
| `search_on_add` | `false` | Trigger search immediately when adding |
| `search_on_update` | `false` | Trigger search when profile is updated |
| `root_folder` | first root | Root folder path for newly added movies |
| `minimum_availability` | `released` | `released` / `announced` / `inCinemas` |

### Plex Watchlist

| Option | Default | Description |
|---|---|---|
| `enabled` | `false` | Enable Plex watchlist sync |
| `url` | required | Local Plex server URL (e.g. `http://plex:32400`) |
| `token` | required | Plex token from Settings › Troubleshooting › Show Token |
| `sync_own` | `true` | Sync your own watchlist |
| `sync_friends` | `false` | Also sync watchlists of Plex Home friends |
| `quality_profile` | required | Exact Radarr quality profile name |
| `add_missing` | `false` | Add watchlisted movies not yet in Radarr |
| `monitored` | `true` | Monitor added movies |
| `search_on_add` | `false` | Trigger search immediately when adding |
| `search_on_update` | `false` | Trigger search when profile is updated |
| `root_folder` | first root | Root folder path for newly added movies |
| `minimum_availability` | `released` | `released` / `announced` / `inCinemas` |

### Ombi

| Option | Default | Description |
|---|---|---|
| `enabled` | `false` | Enable Ombi request sync |
| `url` | required | Ombi server URL (e.g. `http://ombi:3579`) |
| `api_key` | required | API key from Ombi › Settings › Configuration |
| `quality_profile` | required | Exact Radarr quality profile name |
| `approved_only` | `true` | Only sync admin-approved requests (recommended) |
| `add_missing` | `false` | Add requested movies not yet in Radarr |
| `monitored` | `true` | Monitor added movies |
| `search_on_add` | `false` | Trigger search immediately when adding |
| `search_on_update` | `false` | Trigger search when profile is updated |
| `root_folder` | first root | Root folder path for newly added movies |
| `minimum_availability` | `released` | `released` / `announced` / `inCinemas` |

### Retirement queue

Top-level options under `downgrade:`:

| Option | Default | Description |
|---|---|---|
| `enabled` | `false` | Enable the retirement queue |
| `date_source` | `radarr` | Age reference: `plex` (date added to Plex) or `radarr` (date added to Radarr) |
| `upgrade_threshold` | `true` | Prevent sync sources from re-upgrading movies that are at or past the first stage's age threshold |
| `stages` | `[]` | Ordered list of retirement stages (see below) |

#### Retirement stages

Each entry under `downgrade.stages` supports:

| Option | Default | Description |
|---|---|---|
| `action` | `redownload` | What to do: `redownload`, `reencode`, `archive`, or `delete` |
| `older_than_days` | `730` | Only qualify movies added more than N days ago |
| `grace_days` | `7` | Days to wait in queue before the stage executes |
| `quality_profile` | — | Target Radarr quality profile — **required for `redownload` only** |
| `enabled` | `true` | Enable or disable this stage without removing it |

**Stage actions:**

| Action | Requires | What happens |
|---|---|---|
| `redownload` | `quality_profile` | Updates the Radarr profile, deletes the existing file, triggers a new search |
| `reencode` | Tdarr configured | Submits the current file to Tdarr for in-place re-encoding |
| `archive` | Archive configured | Moves the movie folder to the archive directory, unmonitors in Radarr |
| `delete` | Plex configured | Gets the current file path from Plex, deletes the folder from disk, unmonitors in Radarr (entry kept to prevent re-requests) |

Stages are sorted and applied in ascending order of `older_than_days`. Each stage fires independently — a movie can be archived at 2 years and deleted at 5 years regardless of configuration order. Each stage tracks its own completion per movie, so re-running a sync won't re-queue a stage that already executed.

> **Note:** Old flat configs (`method`, `quality_profile`, `older_than_days`, `grace_days` at the top level) are automatically migrated to a single-stage `stages` list on first load. No manual action required.

### Reencode (Tdarr)

Required when any retirement stage uses `action: reencode`.

| Option | Default | Description |
|---|---|---|
| `url` | required | Tdarr server URL (e.g. `http://tdarr:8265`) |
| `library_id` | required | Internal Tdarr library ID — **not** the integer shown in the UI (see below) |
| `path_replace_from` | — | Path prefix as Radarr sees it (e.g. `/movies`) |
| `path_replace_to` | — | Equivalent path prefix as Tdarr sees it (e.g. `/mnt/media/downscale/movies`) |

Path replacement is needed when Radarr and Tdarr mount the same files under different paths inside their containers. Both values have trailing slashes stripped automatically so either form works.

The target Tdarr library must have **Process Library** enabled and a flow or plugin stack configured for downscaling. Folder watching does not need to be enabled — Updatarr submits files directly via the Tdarr API.

### Archive

Required when any retirement stage uses `action: archive`.

| Option | Default | Description |
|---|---|---|
| `path` | required | Archive destination directory as Updatarr sees it (e.g. `/archive/movies`) |
| `path_replace_from` | — | Path prefix as Radarr sees it (e.g. `/movies`) |
| `path_replace_to` | — | Equivalent path prefix as Updatarr sees it (e.g. `/mnt/media/movies`) |

The entire movie folder is moved (not just the video file), preserving subtitles and any sidecar files. The movie is then unmonitored in Radarr so a missing-file scan won't trigger a re-download. If a folder with the same name already exists in the archive, a numeric suffix is appended (`_1`, `_2`, …).

Updatarr must have filesystem read/write access to both the source movies directory and the archive directory. Add both as Docker volume mounts:
```yaml
volumes:
  - /mnt/media/movies:/mnt/media/movies
  - /mnt/media/archive:/archive/movies
```

The `path_replace_from` / `path_replace_to` pair is also used by the **Delete** stage to translate Plex file paths to container-accessible paths. If Plex and Updatarr mount the same storage under different prefixes, set these values under `archive:` and the Delete stage will apply the same translation automatically.

---

## How to find your MDBList list ID

Go to your list on mdblist.com. The ID is in the URL:
`https://mdblist.com/lists/username/my-list/` → the numeric ID is visible in the API URL.

You can also retrieve all your lists via the API:
`https://mdblist.com/api/lists/mine?apikey=YOUR_KEY`

---

## How to find your Tdarr library ID

The library ID is the alphanumeric internal identifier Tdarr assigns to each library (e.g. `aBcDeFgHi`) — it is **not** the integer shown in the Tdarr UI sidebar.

You can find it in the Tdarr UI URL when you navigate to a library:
`http://tdarr:8265/libraries/aBcDeFgHi` → the ID is the last path segment.

Alternatively, use the **Test Connection** button on the Updatarr Settings page under the Tdarr section — it connects to your Tdarr instance and lists all available library IDs along with their names, making it easy to copy the correct one.

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
