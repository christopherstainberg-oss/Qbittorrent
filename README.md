# qBittorrent Auto-Sorter

Automatically categorize the torrents in your **Completed** and **Seeding**
sections and let qBittorrent relocate their data into each category's save path.

You define simple match rules (on torrent name, tracker, save path, size…).
The tool finds finished torrents, assigns each the right **qBittorrent
category**, and turns on *Automatic Torrent Management* so qBittorrent physically
moves the files into that category's configured folder. No files are moved by
this tool directly — qBittorrent does the moving, exactly as it does in the UI.

## How it works

1. Connect to the qBittorrent WebUI API.
2. Fetch torrents matching the configured states (`completed`, `seeding`).
3. For each torrent, apply the first matching rule → a target category.
4. Set the category and enable Automatic Torrent Management, so qBittorrent
   moves the data into that category's save path.

Torrents already in the correct category, or matching no rule (with no
`default_category`), are left untouched.

## Requirements

- Python 3.9+
- qBittorrent with the **WebUI enabled**
  (*Tools → Options → Web UI*), reachable at a host/port you know.
- Each category you reference should have a **save path** set in qBittorrent
  (*right-click a category → Edit category*). That path is where files land.

## Setup

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml   # Windows: copy config.example.yaml config.yaml
```

Then edit `config.yaml`:

- Set `qbittorrent.host`, `username`, `password`.
- Adjust the `rules` to match your library (see comments in the file).
- Leave `dry_run: true` for your first runs.

> ⚠️ `config.yaml` holds your WebUI password — keep it private and don't commit it.

## Usage

```bash
# See your qBittorrent categories and their save paths
python run.py --list-categories

# Preview: show each completed/seeding torrent and the category it would get
python run.py --list-torrents

# Dry run (default while dry_run: true) — logs intended changes, changes nothing
python run.py

# Do it for real
python run.py --apply
```

Useful flags:

| Flag | Effect |
| --- | --- |
| `-c, --config PATH` | Use a different config file (default `config.yaml`). |
| `--dry-run` | Force preview mode regardless of config. |
| `--apply` | Force real changes even if `dry_run: true`. |
| `--list-categories` | Print categories + save paths and exit. |
| `--list-torrents` | Print torrents and their proposed category and exit. |
| `-v, --verbose` | Debug logging (shows skips and non-matches). |

## Web UI

Prefer a browser? Launch the web interface:

```bash
python serve.py                      # http://127.0.0.1:8500
python serve.py --host 0.0.0.0 --port 9000   # expose on your LAN
python serve.py -c other.yaml        # use a different config
```

Then open <http://127.0.0.1:8500>. The page lets you:

- See every **completed / seeding** torrent with its **current** and
  **proposed** category (rows that would change are pre-checked).
- **Preview rules (dry-run)** — log what would happen, change nothing.
- **Apply rules** — set categories and relocate data into each category's
  save path (with a confirmation prompt).
- **Manual sort** — tick any torrents, pick a category, and *Set selected* to
  assign it directly (this is the interactive "user-specified category" flow).
- View your qBittorrent categories and their save paths at a glance.

It reuses the exact same config and sorting logic as the CLI, so rules behave
identically in both. Bind to `127.0.0.1` (the default) unless you specifically
want other machines on your network to reach it.

## Matching rules

Rules are evaluated top-to-bottom; the **first** rule whose conditions all pass
wins. Within a rule every specified condition must match (logical AND):

| Condition | Meaning |
| --- | --- |
| `name_regex` | Case-insensitive regex on the torrent name. |
| `name_contains` | List of substrings; matches if the name contains **any**. |
| `tracker_contains` | Substrings matched against the torrent's tracker URL. |
| `save_path_contains` | Substrings matched against the current save path. |
| `category_is` | Only torrents currently in one of these categories (e.g. `[""]` for uncategorized). |
| `min_size_gb` / `max_size_gb` | Size bounds in GB. |

Set `default_category` to catch everything that matches no rule, or leave it
`null` to skip unmatched torrents.

## Audiobook organizing

Completed torrents in your audiobook category can be normalized so their
on-disk layout matches the qBittorrent name, parsed as `Title - Author`:

- **Single-file** torrent → moved into `Author/Title/Title.<ext>` (folder
  created, file renamed).
- **Folder** torrent → the content folder is renamed to `Author/Title`
  (the files inside are left as-is).

Names are sanitized for Windows/SMB (`:` → ` -`, and `* ? " < > |` stripped).
All renames go through **qBittorrent's own rename API**, so they work on the
machine that actually holds the data (e.g. your NAS) and stay consistent with
qBittorrent's bookkeeping. The operation is **idempotent** — already-organized
torrents are skipped.

Enable it in `config.yaml`:

```yaml
audiobooks:
  enabled: true
  category: "Audiobooks"
  delimiter: " - "                    # name is "Title - Author"
  folder_template: "{author}/{title}"  # fields: {author} {title}
  file_template: "{title}"            # single-file rename (ext preserved)
  sanitize: true
```

Run it (respects `dry_run` — **always preview first**):

```bash
python run.py --organize-audiobooks --dry-run   # preview the exact renames
python run.py --organize-audiobooks --apply      # do it
```

Or use the **Preview audiobooks** / **Organize audiobooks** buttons in the web
UI (they appear when `audiobooks.enabled` is true).

> A torrent whose name isn't in `Title - Author` form is skipped with a note,
> so nothing is renamed on a guess.

## Sonarr / Radarr integration

After completed torrents are categorized, the app can tell Sonarr and Radarr to
import their finished downloads (command `RefreshMonitoredDownloads`, via
`/api/v3/command`). Configure them in `config.yaml`:

```yaml
arr:
  sonarr:
    enabled: true
    url: "http://192.168.0.180:8989"
    api_key: "<Sonarr -> Settings -> General -> API Key>"
    category: "TV-Sonarr"       # triggers when completed torrents have this category
    command: "RefreshMonitoredDownloads"
  radarr:
    enabled: true
    url: "http://192.168.0.180:7878"
    api_key: "<Radarr API key>"
    category: "Movies - Radarr"
    command: "RefreshMonitoredDownloads"
```

URLs and keys can also come from environment variables (handy for Docker so
secrets stay out of the file): `SONARR_URL`, `SONARR_API_KEY`, `SONARR_ENABLED`,
and the `RADARR_*` equivalents.

## Automatic runs (on download completion)

The full pipeline — **categorize → organize audiobooks → notify \*arr** — is
idempotent, so it's safe to run repeatedly. There are three ways to trigger it:

**1. CLI (one-shot, e.g. from cron / Task Scheduler)**

```bash
python run.py --all            # honors dry_run in config
python run.py --all --apply    # force real
```

**2. Poll loop** — the web service / container checks qBittorrent every
`poll.interval_minutes` and runs the pipeline:

```yaml
poll:
  enabled: true
  interval_minutes: 2
```

**3. qBittorrent completion webhook** — instant, event-driven. In qBittorrent:
*Options → Downloads → Run external program on torrent completion*:

```
curl -X POST "http://<container-host>:8500/api/hooks/complete?hash=%I"
```

If you set the `WEBHOOK_TOKEN` env var, append `&token=<that value>`.

Using **both** the poll loop and the webhook (recommended) gives you instant
processing with a periodic safety net.

## Docker (self-hosting)

Everything is packaged as a container that serves the web UI + API + webhook and
runs the poll loop.

```bash
cp config.example.yaml config.yaml    # edit: qBittorrent, rules, arr, audiobooks
docker compose up -d --build
```

Open `http://<host>:8500`. Configuration:

- `config.yaml` is mounted read-only at `/config/config.yaml`.
- Secrets and runtime toggles are set as environment variables in
  `docker-compose.yml` (they override the file):
  `DRY_RUN`, `POLL_ENABLED`, `POLL_INTERVAL_MINUTES`,
  `QBIT_HOST` / `QBIT_USERNAME` / `QBIT_PASSWORD`,
  `SONARR_*`, `RADARR_*`, `WEBHOOK_TOKEN`.

> The container defaults to `DRY_RUN=false` (it acts for real). Set it to
> `"true"` in `docker-compose.yml` for a safe first run, watch the logs
> (`docker compose logs -f`), then flip it back.

Build/run without compose:

```bash
docker build -t qbit-sorter .
docker run -d --name qbit-sorter -p 8500:8500 \
  -v /path/to/config.yaml:/config/config.yaml:ro \
  -e POLL_ENABLED=true -e DRY_RUN=false \
  qbit-sorter
```

## Running on a schedule (Windows Task Scheduler)

Run it automatically, e.g. every 30 minutes:

1. Open **Task Scheduler → Create Task**.
2. **Actions → New**:
   - Program/script: `python`
   - Arguments: `run.py --apply`
   - Start in: `H:\My Drive\Projects\Qbittorrent`
3. **Triggers → New**: *On a schedule* → repeat every 30 minutes.

Or from PowerShell (adjust the path to your `python.exe`):

```powershell
$action  = New-ScheduledTaskAction -Execute "python" -Argument "run.py --apply" -WorkingDirectory "H:\My Drive\Projects\Qbittorrent"
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName "qBittorrent Auto-Sorter" -Action $action -Trigger $trigger
```

## Project layout

```
run.py                 CLI entry point (python run.py ...)
serve.py               Web UI / service entry point (python serve.py)
config.example.yaml    Documented config template — copy to config.yaml
requirements.txt       Python dependencies
Dockerfile             Container image
docker-compose.yml     Self-hosting stack (web UI + webhook + poll loop)
qbit_sorter/
  config.py            Load & validate config (+ env overrides), rule parsing
  client.py            qBittorrent WebUI API wrapper
  rules.py             Torrent model + rule matching
  sorter.py            Build the plan and apply it (path-aware AutoTMM)
  audiobooks.py        Audiobook Author/Title organizing via rename API
  arr.py               Sonarr/Radarr notification (RefreshMonitoredDownloads)
  pipeline.py          One pass: categorize -> audiobooks -> notify *arr
  scheduler.py         Poll loop + serialized pipeline runner
  cli.py               Command-line interface
  web.py               FastAPI web UI (API + webhook + embedded front-end)
```

## Troubleshooting

- **Login failed / can't reach WebUI** — verify host/port, that the WebUI is
  enabled, and the username/password. For self-signed HTTPS set
  `verify_cert: false`.
- **Category '…' does not exist** — create it in qBittorrent (with a save path),
  or set `create_missing_categories: true` in the config.
- **Files aren't moving** — the target category needs a save path in
  qBittorrent, and `enable_autotmm` must be `true` (it is by default). AutoTMM is
  what relocates the data.
