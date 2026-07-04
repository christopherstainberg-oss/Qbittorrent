# Running the UI on Cloudflare Pages — Step by Step

Cloudflare's edge can't reach your LAN qBittorrent directly, so this setup keeps
the **Python backend self-hosted** and puts only the **web UI on Cloudflare
Pages**. The UI talks to your backend through a **Cloudflare Tunnel** — no inbound
ports opened on your network.

```
Browser ──▶ Cloudflare Pages (static UI)
                     │  HTTPS
                     ▼
             Cloudflare Tunnel ──▶ your container (qbit-sorter:8500)
                                        └──▶ qBittorrent / Sonarr / Radarr (LAN)
```

> **Simpler alternative:** you don't strictly need Pages. Once the tunnel is up
> (Part 1), the backend already serves the same UI at your tunnel hostname
> (e.g. `https://qbit-sorter.example.com`). Use Pages only if you want the UI on
> a separate `*.pages.dev` domain. If that's enough, do **Part 1** and stop.

---

## Part 1 — Expose the backend with a Cloudflare Tunnel

You need a Cloudflare account with a domain on Cloudflare (free plan is fine).

1. **Zero Trust dashboard** → **Networks → Tunnels → Create a tunnel** → type
   **Cloudflared** → name it `qbit-sorter` → **Save**.
2. Copy the **tunnel token** shown in the install command (the long string after
   `--token`).
3. Add a **Public Hostname** to the tunnel:
   - **Subdomain:** `qbit-sorter`  **Domain:** `example.com` (your domain)
   - **Service:** `http://qbit-sorter:8500`  ← the container name in the compose
4. On your host, deploy with the tunnel compose file:

   ```bash
   git clone https://github.com/christopherstainberg-oss/Qbittorrent.git
   cd Qbittorrent
   export TUNNEL_TOKEN="<paste the token>"
   export QBIT_PASSWORD="<your qBittorrent password>"
   docker compose -f docker-compose.tunnel.yml up -d --build
   ```

5. Visit `https://qbit-sorter.example.com` — the UI loads over the tunnel.
   (First run is `DRY_RUN=true`; flip it to `false` when ready, see below.)

> **Secure it.** Since this is now internet-reachable, protect it with
> **Cloudflare Access** (Zero Trust → Access → Applications → add your hostname,
> allow only your email). Also set `WEBHOOK_TOKEN` and, once you know your Pages
> URL, `CORS_ORIGINS` (see Part 3).

---

## Part 2 — Deploy the UI to Cloudflare Pages

The UI is a single static file at `qbit_sorter/static/`. Two ways to publish it:

### Option A — Wrangler CLI (quickest)
```bash
npm install -g wrangler
wrangler login
wrangler pages project create qbit-sorter        # pick a production branch, e.g. main
wrangler pages deploy qbit_sorter/static --project-name=qbit-sorter
```
Your UI is now at `https://qbit-sorter.pages.dev`.

### Option B — Git integration (auto-deploy on push)
1. Cloudflare dashboard → **Workers & Pages → Create → Pages → Connect to Git**.
2. Pick the `Qbittorrent` repo.
3. Build settings:
   - **Framework preset:** None
   - **Build command:** *(leave empty)*
   - **Build output directory:** `qbit_sorter/static`
4. **Save and Deploy.** Every `git push` now redeploys the UI.

---

## Part 3 — Point the UI at your backend

Open your Pages URL (`https://qbit-sorter.pages.dev`). On first load it will say
*"not connected"* and jump you to the **Automation** tab.

- In **Backend connection**, enter your tunnel URL
  `https://qbit-sorter.example.com` and click **Save & reconnect**.
- (Or open `https://qbit-sorter.pages.dev/?api=https://qbit-sorter.example.com`
  once — it's saved in your browser.)

The header should now show *connected to … qBittorrent v5.1.4*.

**Lock down CORS** (recommended): set the backend's allowed origin to your Pages
site so only it can call the API. Add to the tunnel compose env and redeploy:
```yaml
    environment:
      CORS_ORIGINS: "https://qbit-sorter.pages.dev"
```

---

## Going live
The tunnel compose starts in `DRY_RUN=true` (preview only). When the logs look
right (`docker compose -f docker-compose.tunnel.yml logs -f`), set `DRY_RUN=false`
and redeploy.

## Security checklist
- [ ] **Cloudflare Access** in front of the tunnel hostname (most important).
- [ ] `CORS_ORIGINS` set to your Pages URL (not `*`).
- [ ] `WEBHOOK_TOKEN` set, and appended to the qBittorrent webhook as
      `&token=...`.
- [ ] qBittorrent WebUI password changed from the default.

## How the UI finds the backend
The UI stores the backend URL in your browser's `localStorage` (set via the
Automation tab or a one-time `?api=` query param). Empty = same origin, which is
why the UI also works unchanged when served directly by the container/tunnel.
