#!/usr/bin/env python3
"""Launch the qBittorrent Auto-Sorter web UI.

    python serve.py                 # http://127.0.0.1:8500
    python serve.py --port 9000 --host 0.0.0.0
    python serve.py -c other.yaml
"""

from __future__ import annotations

import argparse
import logging
import sys

import uvicorn

from qbit_sorter.config import ConfigError
from qbit_sorter.web import create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qbit-sorter-web")
    parser.add_argument("-c", "--config", default="config.yaml",
                        help="Path to config file (default: config.yaml)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind address (default: 127.0.0.1; use 0.0.0.0 for LAN)")
    parser.add_argument("--port", type=int, default=8500, help="Port (default: 8500)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        app = create_app(args.config)
    except ConfigError as exc:
        logging.error("%s", exc)
        return 2

    print(f"\n  qBittorrent Auto-Sorter web UI  →  http://{args.host}:{args.port}\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
