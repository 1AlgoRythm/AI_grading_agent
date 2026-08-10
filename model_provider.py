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


def call_model(
    prompt: str,
    max_tokens: Optional[int] = 512,
    temperature: float = 0.0,
    model: Optional[str] = None,
) -> str:
    """Call the configured model.

    `temperature` and `model` are optional overrides on top of the
    `MODEL_NAME` env var -- callers that need independence from another call
    site using the same provider (e.g. a critic that must not just replay the
    grader's own reasoning) can ask for a different model and/or a higher
    temperature without needing a second BYOK configuration.
    """
    provider = os.getenv("MODEL_PROVIDER")
    key = os.getenv("MODEL_API_KEY")
    name = model or os.getenv("MODEL_NAME")
    if provider == "openai" and key:
        try:
            # Modern (>=1.0) OpenAI SDK client -- the old `openai.ChatCompletion.create` /
            # `openai.api_key = ...` module-level API was removed in openai 1.0 and would
            # otherwise fail here silently (caught below) with no indication why.
            from openai import OpenAI

            client = OpenAI(api_key=key)
            resp = client.chat.completions.create(
                model=name or "gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens or 512,
                temperature=temperature,
            )
            return resp.choices[0].message.content or ""
        except Exception:
            # Fall through to stub on any failure.
            pass
    elif provider == "anthropic" and key:
        try:
            import anthropic

            client = anthropic.Anthropic(api_key=key)
            resp = client.messages.create(
                model=name or "claude-sonnet-5",
                max_tokens=max_tokens or 512,
                temperature=temperature,
                messages=[{"role": "user", "content": prompt}],
            )
            return "".join(block.text for block in resp.content if getattr(block, "type", None) == "text")
        except Exception:
            pass
    # Deterministic fallback for offline/test runs
    return _stub_response(prompt, max_tokens)


def call_model_json(
    prompt: str,
    max_tokens: Optional[int] = 512,
    temperature: float = 0.0,
    model: Optional[str] = None,
) -> dict:
    """Call the model expecting a JSON-serializable dict response.

    The fallback returns a simple dict with `version` and `criteria` when
    parsing fails.
    """
    text = call_model(prompt, max_tokens, temperature=temperature, model=model)
    try:
        return json.loads(text)
    except Exception:
        # heuristic fallback: return a simple structure
        return {"version": 1, "criteria": text}
