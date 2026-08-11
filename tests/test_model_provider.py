"""Tests for model_provider.py's BYOK error handling.

A configured-but-broken provider (bad key, wrong model name, network
outage, or a typo'd MODEL_PROVIDER value) used to fail completely silently:
call_model() would just fall through to the deterministic offline stub with
no indication anything was wrong, so a real deployment could grade
everything with heuristics indefinitely without anyone noticing.
"""
from __future__ import annotations

import sys
import types
import warnings

import pytest

import model_provider


def test_unrecognized_provider_warns_instead_of_silently_stubbing(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "openia")  # typo
    monkeypatch.setenv("MODEL_API_KEY", "some-key")

    with pytest.warns(RuntimeWarning, match="not a recognized provider"):
        result = model_provider.call_model("hello")

    assert result  # still falls back to the stub, just not silently


def test_a_real_call_failure_warns_instead_of_silently_stubbing(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "openai")
    monkeypatch.setenv("MODEL_API_KEY", "fake-key-for-test")

    fake_openai = types.ModuleType("openai")

    class _FakeOpenAI:
        def __init__(self, api_key):
            raise RuntimeError("401 Unauthorized: invalid API key")

    fake_openai.OpenAI = _FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_openai)

    with pytest.warns(RuntimeWarning, match="OpenAI call failed"):
        result = model_provider.call_model("hello")

    assert result  # still falls back to the stub, just not silently


def test_no_provider_configured_at_all_does_not_warn(monkeypatch):
    # The "nothing configured" case is app.py's own sidebar warning, not
    # this one -- calling with no provider set at all is the normal offline
    # path (tests, CI, a demo with no key yet), not a broken configuration.
    monkeypatch.delenv("MODEL_PROVIDER", raising=False)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        model_provider.call_model("hello")

    assert caught == []
