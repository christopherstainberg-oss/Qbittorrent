# Self-hosting on Portainer — Step by Step

Deploy qBittorrent Auto-Sorter as a **Portainer Stack**. No files to create on
the host: the config auto-seeds into a Docker volume on first start, and your
credentials are supplied as environment variables in Portainer.

---

## Before you start

- **Portainer** is installed and managing Docker on a machine that is on the
  **same LAN** as qBittorrent (so it can reach `192.168.0.180`). Your NAS or any
  always-on box works.
- Have your **qBittorrent WebUI password** ready.
- (Optional) **Sonarr/Radarr API keys** — each app → Settings → General → API Key.

---

## Step 1 — Start a new stack
Portainer → **Stacks** → **+ Add stack**.

## Step 2 — Name it
**Name:** `qbit-sorter`

## Step 3 — Build method: Repository
Select **Repository** and fill in:

| Field | Value |
|-------|-------|
| Repository URL | `https://github.com/christopherstainberg-oss/Qbittorrent` |
| Repository reference | `refs/heads/main` |
| Compose path | `docker-compose.portainer.yml` |

Leave authentication off (public repo).

## Step 4 — Environment variables
In the **Environment variables** section, add:

**Required**

| Name | Example value |
|------|---------------|
| `QBIT_HOST` | `http://192.168.0.180:2085` |
| `QBIT_USERNAME` | `admin` |
| `QBIT_PASSWORD` | *your WebUI password* |

**Recommended (start safe)**

| Name | Value |
|------|-------|
| `DRY_RUN` | `true` |
| `POLL_ENABLED` | `true` |
| `POLL_INTERVAL_MINUTES` | `2` |

**Optional — Sonarr / Radarr**

| Name | Example value |
|------|---------------|
| `SONARR_ENABLED` | `true` |
| `SONARR_URL` | `http://192.168.0.180:8989` |
| `SONARR_API_KEY` | *paste key* |
| `RADARR_ENABLED` | `true` |
| `RADARR_URL` | `http://192.168.0.180:7878` |
| `RADARR_API_KEY` | *paste key* |

## Step 5 — Deploy
Click **Deploy the stack**. Portainer clones the repo and builds the image
(first build takes a minute or two).

## Step 6 — Check the safe first run
**Containers → qbit-sorter → Logs**. Expect:

```
[entrypoint] Seeded default config at /config/config.yaml
Connected to qBittorrent v5.1.4 ...
Pipeline done: 0 torrent change(s) ... [dry-run]
```

With `DRY_RUN=true` nothing is changed — it only previews. Confirm it connects
and the proposed actions look right.

## Step 7 — Go live
**Stacks → qbit-sorter**, set `DRY_RUN` to `false`, then **Update the stack**.

## Step 8 — Open the web UI
Browse to **`http://<host-ip>:8500`** (e.g. `http://192.168.0.180:8500`).
Tune everything in the **Rules**, **Categories & Placement**, and **Automation**
tabs — changes persist to the config volume.

## Step 9 — (Optional) Instant trigger on completion
qBittorrent → **Options → Downloads → Run external program on torrent
completion**:

```
curl -X POST "http://<host-ip>:8500/api/hooks/complete?hash=%I"
```

The 2-minute poll already catches completions; this just makes it instant.

---

## Updating later
Portainer → **Stacks → qbit-sorter → Pull and redeploy** (or enable GitOps
auto-update). The config volume is preserved.

## How config & secrets work
- Config lives in the named volume **`qbit_config`**, auto-seeded on first boot
  and kept across restarts/updates.
- Credentials come from the **environment variables** — they override the file,
  so no secrets are stored in Git or the config file.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Could not reach qBittorrent WebUI` | Check `QBIT_HOST`/port; ensure the host can reach `192.168.0.180`. |
| `arr.sonarr is enabled but url/api_key is missing` | You set `SONARR_ENABLED=true` without a key. Add `SONARR_API_KEY` or set enabled `false`. |
| Build fails cloning repo | Re-check the Repository URL; the Portainer host needs internet and image-build ability. |
| Web UI unreachable | Ensure port `8500` is free and the container is running. |

> **Keep it on the LAN.** Don't expose port 8500 to the internet directly. For
> remote access, front it with a Cloudflare Tunnel / reverse proxy.
