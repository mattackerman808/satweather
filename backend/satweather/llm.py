"""The local LLM: vLLM's OpenAI-compatible API on white (salt/vllm).

Used only for wording, never for numbers: the Forecast tab's outlook and
the plain-English AFD (forecast_llm.py) are written from NWS products we
hand it, and every temperature in what comes back must appear in those
products (`check_numbers`), or the output is thrown away.

Output is cached in llm_output per (kind, key) and rebuilt only when its
inputs change (`inputs_hash`, the ids of the products it was written
from). One request per key generates at a time (a Postgres advisory
lock); others are told it's pending. white's chat model is sometimes off
(the PRO 5000 is shared with ComfyUI), so `available()` answers quickly
and callers degrade to showing the products themselves.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Callable

import httpx
from sqlalchemy import text

from satweather.config import get_settings
from satweather.db import get_engine

log = logging.getLogger(__name__)

_avail: tuple[float, bool] | None = None
AVAIL_S = 60


def enabled() -> bool:
    return bool(get_settings().llm_url)


def _headers() -> dict:
    s = get_settings()
    h = {"User-Agent": "satweather"}
    if s.llm_api_key:
        h["Authorization"] = f"Bearer {s.llm_api_key}"
    return h


def available() -> bool:
    """Is the model serving? Cached for a minute."""
    global _avail
    if not enabled():
        return False
    now = time.monotonic()
    if _avail and now - _avail[0] < AVAIL_S:
        return _avail[1]
    s = get_settings()
    try:
        r = httpx.get(f"{s.llm_url.rstrip('/')}/v1/models", headers=_headers(), timeout=3)
        ok = r.status_code == 200 and any(m.get("id") == s.llm_model for m in r.json().get("data", []))
    except (httpx.HTTPError, ValueError):
        ok = False
    _avail = (now, ok)
    return ok


def complete(system: str, user: str, max_tokens: int = 1500) -> dict:
    """One chat completion, as parsed JSON. Raises on transport or JSON errors."""
    s = get_settings()
    payload = {
        "model": s.llm_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        # Qwen thinks by default; this is wording, not reasoning (Tachbase's shape).
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if s.llm_priority:
        payload["priority"] = s.llm_priority
    r = httpx.post(f"{s.llm_url.rstrip('/')}/v1/chat/completions", json=payload, headers=_headers(),
                   timeout=s.llm_timeout_s)
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    return json.loads(_strip_fence(content))


def _strip_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-z]*\s*|\s*```$", "", s)
    return s


# Temperatures as an LLM writes them: "72°", "a high of 72", "lows 45 to 50", "in the 50s" (decade: skipped).
_TEMP = re.compile(r"(-?\d{1,3})\s*°|\b(?:highs?|lows?|temperatures?|near|around)\b(?:\s+(?:of|in|near|around|to|from))*\s+"
                   r"(-?\d{1,3})(?!\d|s\b|%)", re.I)


def numbers_in(obj) -> set[int]:
    """Every integer that appears in the sources, for check_numbers."""
    return {int(n) for n in re.findall(r"-?\d+", json.dumps(obj) if not isinstance(obj, str) else obj)}


def check_numbers(output, allowed: set[int]) -> list[int]:
    """Temperatures in `output` that aren't in `allowed` (empty = fine)."""
    s = json.dumps(output) if not isinstance(output, str) else output
    bad = []
    for m in _TEMP.finditer(s):
        n = int(m.group(1) or m.group(2))
        if n not in allowed:
            bad.append(n)
    return bad


def inputs_hash(parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:32]


def cached(kind: str, key: str, inputs: list, build: Callable[[], tuple[str, str, set[int]]],
           validate: Callable[[dict], str | None] | None = None, max_tokens: int = 1500) -> dict:
    """The cached output for (kind, key), generating it if its inputs changed.

    `build()` returns (system prompt, user prompt, numbers allowed in the
    answer). `validate(content)` returns an error string to reject it.
    Returns {status: ready|pending|unavailable|failed, content, model, created}.
    """
    h = inputs_hash(inputs)
    eng = get_engine()
    with eng.connect() as conn:
        row = conn.execute(text("SELECT inputs_hash, model, created, content, error FROM llm_output "
                                "WHERE kind = :k AND key = :key"), {"k": kind, "key": key}).mappings().first()
    if row and row["inputs_hash"] == h:
        if row["error"]:
            return {"status": "failed", "content": None, "model": row["model"], "created": row["created"]}
        return {"status": "ready", "content": row["content"], "model": row["model"], "created": row["created"]}
    if not available():
        # A stale answer is better than none while the model is off.
        if row and row["content"] and not row["error"]:
            return {"status": "ready", "stale": True, "content": row["content"], "model": row["model"],
                    "created": row["created"]}
        return {"status": "unavailable", "content": None}

    with eng.begin() as conn:
        got = conn.execute(text("SELECT pg_try_advisory_xact_lock(hashtext(:k))"), {"k": f"llm:{kind}:{key}"}).scalar()
        if not got:
            return {"status": "pending", "content": None}
        system, user, allowed = build()
        content, error = None, None
        for attempt in range(2):
            try:
                content = complete(system, user, max_tokens)
            except (httpx.HTTPError, ValueError, KeyError, IndexError) as e:
                # Not cached: the next request tries again.
                log.warning("llm %s/%s: %s: %s", kind, key, type(e).__name__, e)
                return {"status": "unavailable", "content": None}
            bad = check_numbers(content, allowed)
            error = f"numbers not in sources: {sorted(set(bad))}" if bad else (validate(content) if validate else None)
            if not error:
                break
            log.warning("llm %s/%s attempt %d rejected: %s", kind, key, attempt + 1, error)
            user = (user + "\n\nYour previous answer was rejected (" + error +
                    "). Use only numbers that appear in the products above.")
            content = None
        model = get_settings().llm_model
        conn.execute(text("""
            INSERT INTO llm_output (kind, key, inputs_hash, model, created, content, error)
            VALUES (:k, :key, :h, :m, now(), CAST(:c AS jsonb), :e)
            ON CONFLICT (kind, key) DO UPDATE SET inputs_hash = EXCLUDED.inputs_hash, model = EXCLUDED.model,
                created = EXCLUDED.created, content = EXCLUDED.content, error = EXCLUDED.error"""),
            {"k": kind, "key": key, "h": h, "m": model, "c": json.dumps(content) if content else None, "e": error})
    if error:
        return {"status": "failed", "content": None, "model": model}
    return {"status": "ready", "content": content, "model": model}
