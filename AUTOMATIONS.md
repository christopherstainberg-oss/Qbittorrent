# Automations — Step by Step

Automations are user-defined **"if this, then that"** rules for your torrents:
when a torrent matches the conditions you set, the app runs one or more actions
on it — set a category, change file/queue priority, add a tag, or move it.

They run **automatically** every time the pipeline runs (the poll loop and the
qBittorrent completion webhook) and **on demand** from the buttons in the tab.

> Web UI → **Automations** tab.

---

## Concepts

An automation has three parts:

1. **Match conditions** — the "if". Every condition you fill in must pass (AND).
   Leave a field blank to ignore it. These are the same conditions as the Rules
   tab.
2. **Actions** — the "then". One or more actions, run top-to-bottom on each
   matching torrent. You can add as many as you like.
3. **Only completed torrents** — a safety toggle (on by default) that limits the
   automation to fully-downloaded torrents, so it never touches active
   downloads. Turn it off to act on downloading torrents too (e.g. to set queue
   priority while they download).

---

## Step by step: create a new automation

1. Open the **Automations** tab and click **➕ Add automation**. A card appears.
2. **Name** it something memorable, e.g. `Tag & prioritize 4K remuxes`.
3. Leave **Enabled** checked (uncheck to keep it saved but inactive).
4. Decide **Only completed torrents**:
   - *Checked* (default) — acts only on finished torrents. Best for category /
     move / tag actions.
   - *Unchecked* — also acts on downloading torrents. Use for queue priority.
5. Fill in the **match conditions** you want (any combination):
   - **Name contains (any)** — comma-separated words; matches if the torrent
     name contains any of them. e.g. `remux, bluray`.
   - **Name regex** — a regular expression for finer control. e.g. `s\d+e\d+`.
   - **Tracker contains** / **Save path contains** — match on tracker URL or
     current save path.
   - **Only if category is** — restrict to torrents already in these categories
     (comma-separated; use an empty value to mean "uncategorized").
   - **Min / Max size (GB)** — size bounds.
6. Under **When matched, do these actions**, set the first action's **type**
   (the left dropdown) and its **value** (the control to the right — it changes
   to fit the action; see the table below).
7. Click **➕ Add action** to add more actions to the *same* automation. Each new
   row is another action + value applied to every matching torrent. Remove one
   with the **✕** button (you must keep at least one).
8. Click **Save**.
9. Click **Preview** to see what *would* happen (matched counts per automation,
   nothing changes). When it looks right, click **Run now ▶** to apply — or just
   let the poll/webhook run it automatically.

The **log panel** at the bottom shows, per automation, how many torrents matched
and what each action did.

---

## Conditions reference

| Field | Meaning |
|-------|---------|
| Name contains (any) | Torrent name contains **any** of these (comma-separated), case-insensitive |
| Name regex | Torrent name matches this regular expression (case-insensitive) |
| Tracker contains | Tracker URL contains any of these |
| Save path contains | Current save path contains any of these |
| Only if category is | Torrent's current category is one of these (empty = uncategorized) |
| Min size (GB) / Max size (GB) | Torrent size bounds |

An automation with **no** conditions matches **every** torrent — combine with
`Only completed torrents` carefully.

## Actions reference

| Action | Value control | What it does |
|--------|---------------|--------------|
| **Set category** | category dropdown | Assigns the category (skips torrents already in it) |
| **File priority** | Normal / High / Maximum / Do not download | Sets the download priority of **all files** in the torrent |
| **Queue priority** | Top / Up / Down / Bottom | Moves the torrent in the download/seed queue (needs Torrent Queueing on) |
| **Add tag** | text | Adds a tag (skips torrents that already have it) |
| **Set location** | path | Physically moves the data to that folder (must be writable by qBittorrent) |

Actions run in the order shown. Most are **idempotent** — running an automation
again won't duplicate work (e.g. it won't re-tag an already-tagged torrent).

---

## Worked examples

### 1. Tag *and* categorize Linux ISOs
- **Name contains:** `ubuntu, debian, fedora`
- **Only completed torrents:** off (tag them even while downloading)
- **Actions:**
  1. Add tag → `linux`
  2. Set category → `Linux`

### 2. Maximize small remuxes, and push them to the top of the queue
- **Name contains:** `remux`
- **Max size (GB):** `20`
- **Only completed torrents:** off
- **Actions:**
  1. File priority → `Maximum`
  2. Queue priority → `Top`

### 3. Move finished audiobooks to a folder
- **Only if category is:** `Audiobooks`
- **Only completed torrents:** on
- **Actions:**
  1. Set location → `/Torrents/Audiobooks`

> To relocate to a library **outside** qBittorrent's reach (e.g. a NAS share it
> can't write), use **Library relocation** on the Automation tab instead — that
> engine moves files itself. See `config.example.yaml`.

---

## How it runs

- **Automatically:** each pipeline pass (the poll loop and the completion
  webhook) evaluates every enabled automation. Because the actions are
  idempotent, running often is safe.
- **On demand:** the **Preview** (dry-run) and **Run now** buttons.
- **Dry-run:** when the app is in dry-run mode, automations only report what they
  *would* do.

## Notes

- Automations are stored in `config.yaml` under `automations:` and can also be
  edited there directly (see the commented examples in `config.example.yaml`).
- Changes made in Docker/Portainer take effect after the container reloads the
  config (the UI reloads it automatically on save).
- Order matters within an automation: actions run top-to-bottom.
