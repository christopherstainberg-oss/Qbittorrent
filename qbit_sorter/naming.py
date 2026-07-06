"""Sonarr/Radarr-style renaming — without a metadata provider.

Parses release tokens straight out of a torrent/file name (title, year,
quality, source, codec, season/episode, group) and renders a user-defined
template into a destination path. Used by the relocator to name the library
copy; the seeding copy in the download folder is never touched.

Templates use {token} placeholders, e.g.:
    folder:  "{title} ({year})"
    file:    "{title} ({year}) [{quality}]"
Empty tokens are dropped and leftover "()" / "[]" / stray separators cleaned up,
so a movie with no year still renders tidily.
"""

from __future__ import annotations

import os
import re

_SEP = re.compile(r"[._]+")
_YEAR = re.compile(r"\b(19\d{2}|20\d{2})\b")
_QUALITY = re.compile(r"\b(2160p|1080p|720p|480p|4k)\b", re.I)
_SOURCE = re.compile(
    r"\b(remux|blu-?ray|bd-?rip|br-?rip|web-?dl|web-?rip|webrip|hdtv|dvd-?rip|hd-?rip|web)\b", re.I)
_CODEC = re.compile(r"\b(x26[45]|h[\s.]?26[45]|hevc|avc|av1|xvid)\b", re.I)
_SXXEXX = re.compile(r"\bS(\d{1,2})E(\d{1,2})\b", re.I)
_GROUP = re.compile(r"-([A-Za-z0-9]+)$")
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_TOKEN = re.compile(r"\{(\w+)\}")

TOKENS = ("title", "author", "year", "quality", "source", "codec", "group",
          "season", "episode", "category", "name", "ext")


def parse(name: str, category: str = "") -> dict:
    """Extract release tokens from a torrent/file name."""
    stem = os.path.splitext(name)[0] if _looks_like_file(name) else name

    g = _GROUP.search(stem)
    q = _QUALITY.search(stem)
    s = _SOURCE.search(stem)
    c = _CODEC.search(stem)
    y = _YEAR.search(stem)
    se = _SXXEXX.search(stem)

    # A name "looks like a release" when it carries quality/source/codec/episode
    # tags. For those, the title ends at the first marker (year included). For
    # everything else — audiobook-style "Title - Author" — a stray year must not
    # truncate the title, and the part after the last " - " is the author.
    looks_like_release = bool(q or s or c or se)
    author = ""
    if looks_like_release:
        markers = [m.start() for m in (y, q, s, c, se) if m]
        cut = min(markers) if markers else len(stem)
        title = _SEP.sub(" ", stem[:cut]).strip(" -")
    elif " - " in stem:
        head, author = stem.rsplit(" - ", 1)
        title = _SEP.sub(" ", head).strip(" -")
        author = author.strip()
    else:
        title = _SEP.sub(" ", stem).strip(" -")

    full = _SEP.sub(" ", stem).strip()
    return {
        "name": full,
        "title": title or full,
        "author": author,
        "year": y.group(1) if y else "",
        "quality": "2160p" if (q and q.group(1).lower() == "4k") else (q.group(1) if q else ""),
        "source": (s.group(1) if s else ""),
        "codec": re.sub(r"[\s.]", "", c.group(1)) if c else "",
        "group": g.group(1) if g else "",
        "season": se.group(1).zfill(2) if se else "",
        "episode": se.group(2).zfill(2) if se else "",
        "category": category,
    }


def _looks_like_file(name: str) -> bool:
    ext = os.path.splitext(name)[1].lower()
    return 1 < len(ext) <= 5 and ext[1:].isalnum()


def _sanitize(value: str) -> str:
    return _ILLEGAL.sub("", str(value)).strip()


def render(template: str, tokens: dict) -> str:
    """Fill a template and clean up gaps left by empty tokens."""
    out = _TOKEN.sub(lambda m: _sanitize(tokens.get(m.group(1), "")), template)
    out = re.sub(r"\s{2,}", " ", out)          # collapse runs left by empty tokens
    out = re.sub(r"\(\s+", "(", out)           # trim spaces just inside ( )
    out = re.sub(r"\s+\)", ")", out)
    out = re.sub(r"\[\s+", "[", out)           # ...and inside [ ]
    out = re.sub(r"\s+\]", "]", out)
    out = re.sub(r"\(\s*\)", "", out)          # drop now-empty ()
    out = re.sub(r"\[\s*\]", "", out)          # ...and []
    out = re.sub(r"\s*-\s*$", "", out)         # trailing " - "
    out = re.sub(r"\s{2,}", " ", out)          # collapse again after removals
    out = re.sub(r"\s*([/\\])\s*", r"\1", out)  # tidy around path separators
    out = re.sub(r"[/\\]{2,}", "/", out)        # collapse empty path segments
    return out.strip(" .-/\\")                  # never leading/trailing separators


def destination_subpath(folder_template: str, file_template: str,
                        tokens: dict, is_file: bool, ext: str,
                        original_basename: str) -> str:
    """Compute the path (relative to the destination root) for a relocated item.

    Single file -> "<folder>/<file><ext>". Folder torrent -> "<folder>" (its
    contents are placed inside, names preserved). Empty templates fall back to
    the original name, so naming is fully optional per destination."""
    folder = render(folder_template, tokens) if folder_template else ""
    if is_file:
        stem = render(file_template, tokens) if file_template else os.path.splitext(original_basename)[0]
        filename = (stem or os.path.splitext(original_basename)[0]) + ext
        return os.path.join(folder, filename) if folder else filename
    # folder torrent: rename the top-level folder
    name = folder or (render(file_template, tokens) if file_template else "") or original_basename
    return name
