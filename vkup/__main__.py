#!/usr/bin/env python3
"""Единая точка входа: python -m vkup [--mock] [--autostart]"""

from .server import main

if __name__ == "__main__":
    raise SystemExit(main())
