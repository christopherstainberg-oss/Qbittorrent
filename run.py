#!/usr/bin/env python3
"""Entry point: `python run.py [options]`. See `python run.py --help`."""

import sys

from qbit_sorter.cli import main

if __name__ == "__main__":
    sys.exit(main())
