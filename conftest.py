"""Make the repo root importable so `import openwiki` works without installing.

(If you `pip install -e .` this is redundant, but it keeps a bare checkout
runnable with a plain `pytest`.)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
