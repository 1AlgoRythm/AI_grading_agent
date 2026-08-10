"""Simple BYOK-capable model caller with a deterministic fallback.

This module exposes `call_model(prompt, max_tokens=...)` which will attempt
to use a real provider if environment variables are set (currently supports a
minimal OpenAI path if `MODEL_PROVIDER=="openai"`), otherwise falls back to
a deterministic stub used by tests and offline runs.
"""
from __future__ import annotations

import os
import json
import time
from typing import Callable, Optional, TypeVar

from dotenv import load_dotenv

T = TypeVar("T")


def _retry(fn: Callable[[], T], max_retries: int = 2, base_delay: float = 0.5) -> T:
    """Call `fn()`, retrying a couple of times with a short backoff before
    giving up. Without this, one transient error (a brief rate limit, a
    flaky connection) permanently demotes that one real LLM call to the
    offline stub, with no chance to recover -- which is exactly what made
    real Gemini calls look broken during heavy testing when they weren't."""
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                time.sleep(base_delay * (2 ** attempt))
    raise last_exc  # type: ignore[misc]

# Docker Compose already reads .env for its container's environment; this
# makes plain `python` runs (skeleton.py, the p1/p2/p3 apps, ad-hoc scripts)
# behave the same way instead of requiring `export` in every shell session.
# Only fills in variables that aren't already set, so a real `export` in the
# current shell still wins over whatever's in .env.
load_dotenv()


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
            openai_model = name or "gpt-4o-mini"
            try:
                # Legacy `max_tokens` -- works on gpt-4o/gpt-4.1-era models.
                # Not retried: a model that rejects this parameter rejects it
                # every time, so retrying would just waste calls.
                resp = client.chat.completions.create(
                    model=openai_model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=max_tokens or 512,
                    temperature=temperature,
                )
            except Exception:
                # Newer models (the o1/o3/gpt-5.x reasoning-model lineage)
                # renamed this to `max_completion_tokens` and reject the old
                # name outright with a 400 -- retry with the new name. This
                # path IS worth retrying: could be that rename, or a
                # transient error, and every current model accepts this form.
                resp = _retry(lambda: client.chat.completions.create(
                    model=openai_model,
                    messages=[{"role": "user", "content": prompt}],
                    max_completion_tokens=max_tokens or 512,
                    temperature=temperature,
                ))
            return resp.choices[0].message.content or ""
        except Exception:
            # Fall through to stub on any failure.
            pass
    elif provider == "anthropic" and key:
        try:
            import anthropic

            client = anthropic.Anthropic(api_key=key)
            resp = _retry(lambda: client.messages.create(
                model=name or "claude-sonnet-5",
                max_tokens=max_tokens or 512,
                temperature=temperature,
                messages=[{"role": "user", "content": prompt}],
            ))
            return "".join(block.text for block in resp.content if getattr(block, "type", None) == "text")
        except Exception:
            pass
    elif provider in ("gemini", "google") and key:
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=key)
            gemini_model = name or "gemini-flash-latest"
            base_config = {
                "temperature": temperature,
                "max_output_tokens": max_tokens or 512,
            }
            try:
                # Gemini's newer "thinking" models spend part of
                # max_output_tokens on internal reasoning before answering by
                # default, which can eat a small budget (e.g. the grader/
                # critic's JSON prompts) down to nothing. Grading needs a
                # direct answer within a known token budget (plan §6), not a
                # reasoning trace, so try turning thinking off first. Not
                # retried: a model that rejects this parameter (some 400
                # INVALID_ARGUMENT if you even mention it) will reject it
                # every time, so retrying here would just waste calls before
                # falling through to the path that actually works.
                resp = client.models.generate_content(
                    model=gemini_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        **base_config,
                        thinking_config=types.ThinkingConfig(thinking_budget=0),
                    ),
                )
            except Exception:
                # Either the model doesn't accept thinking_config, or a
                # transient error hit -- either way, this path is worth
                # retrying since it's the one every Gemini model accepts.
                resp = _retry(lambda: client.models.generate_content(
                    model=gemini_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(**base_config),
                ))
            return resp.text or ""
        except Exception:
            pass
    # Deterministic fallback for offline/test runs
    return _stub_response(prompt, max_tokens)


def _extract_json(text: str) -> Optional[dict]:
    """Best-effort JSON extraction from a real model's text response.

    Real models routinely ignore "respond with ONLY JSON" and wrap the
    object in a ```json fenced code block or add a sentence of preamble --
    a bare `json.loads` on the raw text fails on all of that and silently
    demotes every real response to the offline fallback, which defeats the
    point of having a real BYOK key configured. Tries, in order: the raw
    text as-is, the text with a fenced code block stripped, and the first
    balanced {...} substring found anywhere in the text.
    """
    candidates = [text.strip()]

    import re
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1).strip())

    start = text.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(text[start:], start=start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start:i + 1])
                    break

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


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
    parsed = _extract_json(text)
    if parsed is not None:
        return parsed
    # heuristic fallback: return a simple structure
    return {"version": 1, "criteria": text}
