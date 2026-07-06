# Renaming and organizing (Sonarr/Radarr-style)

When Library relocation moves a completed torrent into your library, it can also
**rename and organize** the copy — the way Sonarr/Radarr rename on import. It's
optional, per destination, and driven by templates you customize.

Two important differences from *arr:

- **No metadata lookup.** There's no TVDB/TMDB. Tokens are parsed straight from
  the torrent/file **name**, so quality of results depends on how the release is
  named. A clean scene name (`The.Martian.2015.1080p.BluRay.x265-RARBG`) parses
  well; a bare `Title - Author` yields just the title.
- **Only the library copy is renamed.** The seeding copy in your download folder
  is never touched (hardlink/copy makes a second name at the destination), so
  seeding keeps working.

---

## Where to set it

Integrations tab → **Library relocation** → a destination → the **Folder
template** and **File template** fields. A live preview shows the result as you
type. Leave both blank to keep the original name.

- **Folder template** — the folder(s) created under the destination path.
- **File template** — the filename (the extension is added automatically).

Single-file torrents become `‹folder›/‹file›.ext`. Folder torrents get their
top-level folder renamed to `‹folder›` (contents kept as-is).

---

## Tokens

Parsed from the torrent name:

| Token | Example | Notes |
|-------|---------|-------|
| `{title}` | `The Martian` | Release name up to the first tag; for `Title - Author` names, the part before the last ` - ` |
| `{author}` | `Andy Weir` | Audiobook-style names only: the part after the last ` - ` |
| `{year}` | `2015` | First 19xx/20xx found |
| `{quality}` | `1080p` | 2160p / 1080p / 720p / 480p (4k → 2160p) |
| `{source}` | `BluRay` | BluRay / WEB-DL / WEBRip / HDTV / Remux / … |
| `{codec}` | `x265` | x265 / x264 / HEVC / AV1 / … |
| `{season}` | `05` | from `SxxExx`, zero-padded |
| `{episode}` | `14` | from `SxxExx`, zero-padded |
| `{group}` | `RARBG` | release group after the trailing `-` |
| `{category}` | `Movies` | the destination's category |
| `{name}` | `The Martian 2015 1080p …` | full name, separators cleaned |
| `{ext}` | `.mkv` | file extension (usually added automatically) |

Empty tokens are dropped and leftover `()`, `[]`, and stray separators are
cleaned up — so a movie with no year still renders tidily.

---

## Example templates

**Movies**
```
Folder:  {title} ({year})
File:    {title} ({year}) [{quality} {source} {codec}]
```
→ `The Martian (2015)/The Martian (2015) [1080p BluRay x265].mkv`

**TV**
```
Folder:  {title}/Season {season}
File:    {title} - S{season}E{episode} [{quality} {source} {codec}]
```
→ `Breaking Bad/Season 05/Breaking Bad - S05E14 [1080p WEB-DL x264].mkv`

**Audiobooks** (name like `Title - Author`) — Author/Title layout
```
Folder:  {author}/{title}
File:    {title}
```
→ `Kristan Higgins/Always the Last to Know/Always the Last to Know.m4b`
(names with no ` - ` fall back to just the title folder)

---

## Notes

- Illegal filename characters (`<>:"/\|?*`) are stripped from token values; the
  template's own `/` still creates folders.
- For folder torrents, only the top folder is renamed; inner files keep their
  names (there's no metadata to know which file is the "main" one).
- Templates live in `config.yaml` under each `relocation.destinations` entry
  (`folder_template` / `file_template`) and can be edited there too.
