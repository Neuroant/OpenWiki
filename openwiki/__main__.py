"""Entry point for ``python -m openwiki``."""

import sys

from openwiki.cli import main

if __name__ == "__main__":
    sys.exit(main())
