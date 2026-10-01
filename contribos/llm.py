"""Optional LLM layer. Off by default; ContribOS works fully without it.

Set CONTRIBOS_LLM=anthropic (ANTHROPIC_API_KEY) or CONTRIBOS_LLM=openai
(OPENAI_API_KEY), and optionally CONTRIBOS_MODEL. The model only ever
explains evidence ContribOS already gathered, and any sentence that names
a file not in that evidence is dropped before you see it.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

DEFAULT_MODELS = {"anthropic": "claude-sonnet-5", "openai": "gpt-5"}

SYSTEM = (
    "You help a new open-source contributor understand an issue. Use ONLY the evidence given. "
    "Cite files exactly as written in the evidence, in backticks. If the evidence doesn't support a claim, "
    "say what the contributor should check instead of guessing. Be brief and concrete. "
    "Never write the fix or the PR for them."
)


class LLMUnavailable(RuntimeError):
    pass


def provider() -> str | None:
    p = os.environ.get("CONTRIBOS_LLM", "").strip().lower()
    return p if p in DEFAULT_MODELS else None


def complete(prompt: str, system: str = SYSTEM, max_tokens: int = 700) -> str:
    p = provider()
    if not p:
        raise LLMUnavailable("Set CONTRIBOS_LLM=anthropic or openai to enable AI summaries.")
    model = os.environ.get("CONTRIBOS_MODEL", DEFAULT_MODELS[p])
    if p == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY") or _fail("ANTHROPIC_API_KEY is not set.")
        base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
        data = _post(f"{base}/v1/messages",
                     {"x-api-key": key, "anthropic-version": "2023-06-01"},
                     {"model": model, "max_tokens": max_tokens, "system": system,
                      "messages": [{"role": "user", "content": prompt}]})
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    key = os.environ.get("OPENAI_API_KEY") or _fail("OPENAI_API_KEY is not set.")
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    data = _post(f"{base}/chat/completions", {"Authorization": f"Bearer {key}"},
                 {"model": model, "max_completion_tokens": max_tokens,
                  "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]})
    return data["choices"][0]["message"]["content"]


def _fail(msg: str):
    raise LLMUnavailable(msg)


def _post(url: str, headers: dict, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise LLMUnavailable(f"LLM request failed: HTTP {e.code} {e.read()[:200]!r}") from e
    except urllib.error.URLError as e:
        raise LLMUnavailable(f"LLM request failed: {e.reason}") from e


def grounded(text: str, allowed_paths: set[str]) -> tuple[str, int]:
    """Drop sentences that cite a path outside the evidence. Returns (text, dropped)."""
    kept, dropped = [], 0
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        cited = re.findall(r"`([\w./-]+\.\w+)`", sentence)
        if any(c not in allowed_paths and not any(p.endswith(c) for p in allowed_paths) for c in cited):
            dropped += 1
            continue
        kept.append(sentence)
    return " ".join(kept), dropped


def explain_issue(title: str, body: str, evidence_md: str, allowed_paths: set[str]) -> str:
    prompt = (f"ISSUE TITLE: {title}\n\nISSUE BODY:\n{body[:4000]}\n\nEVIDENCE GATHERED FROM THE REPO:\n"
              f"{evidence_md[:8000]}\n\nWrite three short paragraphs: (1) what the issue is asking, in plain "
              "words; (2) the most likely place and reason for the behaviour, citing the evidence; (3) what the "
              "contributor should verify first.")
    text, dropped = grounded(complete(prompt), allowed_paths)
    if dropped:
        text += f"\n\n_({dropped} sentence(s) removed because they cited files outside the evidence.)_"
    return text
