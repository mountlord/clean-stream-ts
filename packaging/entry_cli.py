# packaging/entry_cli.py
"""Bootloader entry point for the console exe (CleanStreamTS-cli.exe)."""
import sys

from cleanstreamts.cli import main

if __name__ == "__main__":
    sys.exit(main())
