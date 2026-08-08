"""Simple BYOK-capable model caller with a deterministic fallback.

This module exposes `call_model(prompt, max_tokens=...)` which will attempt
to use a real provider if environment variables are set (currently supports a
minimal OpenAI path if `MODEL_PROVIDER=="openai"`), otherwise falls back to
a deterministic stub used by tests and offline runs.
"""
from __future__ import annotations

import os
import json
from typing import Optional


def _stub_response(prompt: str, max_tokens: int | None = None) -> str:
    # Keep the stub short and deterministic: echo a short extracted answer if
    # the prompt includes a clear "Answer:" marker; otherwise return a
    # structured, human-readable draft.
    if "Final answer:" in prompt:
        # attempt to find a numeric-looking answer in the prompt
        import re
        m = re.search(r"Final answer:\s*([\-]?[0-9]+(?:\.[0-9]+)?)", prompt)
        if m:
            return m.group(1)
        return "42"
    # Otherwise produce a short solution and rubric suggestion.
    return (
        "[auto-draft solution] Show algebraic manipulation to isolate the variabl"
        "e; check arithmetic.\n---\nProposed answer: 2\n"
    )


def call_model(prompt: str, max_tokens: Optional[int] = 512) -> str:
    provider = os.getenv("MODEL_PROVIDER")
    key = os.getenv("MODEL_API_KEY")
    name = os.getenv("MODEL_NAME")
    # Minimal OpenAI path if requested (non-blocking if package absent).
    if provider == "openai" and key:
        try:
            import openai

            openai.api_key = key
            resp = openai.ChatCompletion.create(
                model=name or "gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens or 512,
                temperature=0.0,
            )
            return resp.choices[0].message.content
        except Exception:
            # Fall through to stub on any failure.
            pass
    # Deterministic fallback for offline/test runs
    return _stub_response(prompt, max_tokens)


def call_model_json(prompt: str, max_tokens: Optional[int] = 512) -> dict:
    """Call the model expecting a JSON-serializable dict response.

    The fallback returns a simple dict with `version` and `criteria` when
    parsing fails.
    """
    text = call_model(prompt, max_tokens)
    try:
        return json.loads(text)
    except Exception:
        # heuristic fallback: return a simple structure
        return {"version": 1, "criteria": text}
