"""Put the repository root on sys.path so bare `pytest` can import the
top-level modules (contracts, fixtures, lanes, app). Invoked as `pytest`
rather than `python -m pytest`, the CWD is not added automatically."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(autouse=True)
def _no_real_model_provider_in_tests(monkeypatch):
    """Tests must stay deterministic and offline regardless of local config.

    `model_provider.py` auto-loads `.env` so real BYOK usage doesn't need a
    manual `export` -- but that means a developer's real API key would
    otherwise leak into every test run: slow, burns real quota, and
    non-deterministic (a real model's judgment varies between runs, which
    breaks tests written against the fixed offline-fallback output). Force
    every test to see the offline stub path, no matter what's configured
    locally for manual testing.
    """
    for var in ("MODEL_PROVIDER", "MODEL_API_KEY", "MODEL_NAME", "CRITIC_MODEL_NAME", "CRITIC_TEMPERATURE"):
        monkeypatch.delenv(var, raising=False)
