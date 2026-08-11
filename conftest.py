"""Put the repository root on sys.path so bare `pytest` can import the
top-level modules (contracts, fixtures, lanes, app). Invoked as `pytest`
rather than `python -m pytest`, the CWD is not added automatically."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

# `model_provider.py` calls `load_dotenv()` at import time, and load_dotenv()
# fills in any env var that ISN'T already set. The fixture below deletes the
# model env vars before each test body runs -- but if that particular test is
# the *first* one in the whole session to import model_provider (directly or
# via lanes.p1_solution etc.), the import re-runs load_dotenv() mid-test,
# which sees the vars are now unset and refills them straight from a real
# local .env, *after* the fixture already ran. That silently sent at least
# one real, cost-incurring OpenAI call during local test runs. Neutralizing
# load_dotenv() itself, here, before any test module (and therefore
# model_provider) is ever imported, closes the hole regardless of import
# order.
import dotenv

dotenv.load_dotenv = lambda *args, **kwargs: None


@pytest.fixture(autouse=True)
def _no_real_model_provider_in_tests(monkeypatch):
    """Tests must stay deterministic and offline regardless of local config.

    Force every test to see the offline stub path, no matter what's
    configured locally for manual testing.
    """
    for var in ("MODEL_PROVIDER", "MODEL_API_KEY", "MODEL_NAME", "CRITIC_MODEL_NAME", "CRITIC_TEMPERATURE"):
        monkeypatch.delenv(var, raising=False)
