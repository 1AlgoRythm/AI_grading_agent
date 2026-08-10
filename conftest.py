"""Put the repository root on sys.path so bare `pytest` can import the
top-level modules (contracts, fixtures, lanes, app). Invoked as `pytest`
rather than `python -m pytest`, the CWD is not added automatically."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
