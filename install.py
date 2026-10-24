#!/usr/bin/env python3
"""Compatibility entry point: initialize configuration without overwriting user files."""
import sys
from daily_news.cli import main

if __name__ == "__main__":
    raise SystemExit(main([*sys.argv[1:], "init"]))
