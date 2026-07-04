"""qBittorrent Auto-Sorter — categorize completed/seeding torrents and let
qBittorrent relocate their data into each category's save path."""

__version__ = "1.0.0"


def ensure_utf8_console() -> None:
    """Force stdout/stderr to UTF-8 so logging torrent names / arrows never
    crashes on a legacy Windows console (cp1252). Safe no-op elsewhere."""
    import sys

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass
