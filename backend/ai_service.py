"""
TradePro Backend - Multi-provider AI Chat Service
Priority: Claude -> OpenAI -> Gemini (free fallback, self-updating model).
Configure via environment variables:
  ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY
  AI_PROVIDER (optional override: "claude" | "openai" | "gemini")
  GEMINI_MODEL (optional manual override, otherwise auto-detected)
"""

import os
import re
import json
import time
import logging
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)


def _post_json(url: str, payload: dict, headers: dict, timeout: int = 30) -> dict:
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _get_json(url: str, headers: dict, timeout: int = 15) -> dict:
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def chat_claude(messages: list, system_prompt: str) -> dict:
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        return {"success": False, "error": "ANTHROPIC_API_KEY not set"}
    model = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")
    payload = {
        "model"     : model,
        "max_tokens": 1000,
        "system"    : system_prompt,
        "messages"  : [{"role": m["role"], "content": m["content"]} for m in messages],
    }
    headers = {
        "Content-Type"     : "application/json",
        "anthropic-version": "2023-06-01",
        "x-api-key"        : key,
    }
    try:
        resp = _post_json("https://api.anthropic.com/v1/messages", payload, headers)
        return {"success": True, "text": resp["content"][0]["text"], "provider": "claude"}
    except Exception as e:
        logger.error(f"Claude chat error: {e}")
        return {"success": False, "error": str(e)}


def chat_openai(messages: list, system_prompt: str) -> dict:
    key = os.getenv("OPENAI_API_KEY", "")
    if not key:
        return {"success": False, "error": "OPENAI_API_KEY not set"}
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    payload = {
        "model"   : model,
        "messages": [{"role": "system", "content": system_prompt}] +
                    [{"role": m["role"], "content": m["content"]} for m in messages],
    }
    headers = {
        "Content-Type" : "application/json",
        "Authorization": f"Bearer {key}",
    }
    try:
        resp = _post_json("https://api.openai.com/v1/chat/completions", payload, headers)
        return {"success": True, "text": resp["choices"][0]["message"]["content"], "provider": "openai"}
    except Exception as e:
        logger.error(f"OpenAI chat error: {e}")
        return {"success": False, "error": str(e)}


# ─── Gemini: self-updating model discovery ─────────────────────────────────

_GEMINI_MODEL_CACHE: dict = {"model": None, "checked_at": 0}
_GEMINI_CACHE_TTL   = 6 * 3600  # re-check every 6 hours

# Hand-written fallback order, used only if live discovery itself fails
_GEMINI_FALLBACK_CANDIDATES = [
    "gemini-3.5-flash",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
]


def _discover_gemini_model(key: str) -> str | None:
    """Ask Google which models currently support generateContent, and pick the
    best available 'flash' model automatically. Result is cached in-memory."""
    now = time.time()
    if _GEMINI_MODEL_CACHE["model"] and (now - _GEMINI_MODEL_CACHE["checked_at"] < _GEMINI_CACHE_TTL):
        return _GEMINI_MODEL_CACHE["model"]

    try:
        resp = _get_json(
            "https://generativelanguage.googleapis.com/v1beta/models",
            {"x-goog-api-key": key},
        )
        names = []
        for m in resp.get("models", []):
            methods = m.get("supportedGenerationMethods", [])
            if "generateContent" in methods:
                name = m.get("name", "").replace("models/", "")
                if name:
                    names.append(name)

        def version_key(n: str):
            match = re.search(r"gemini-(\d+)\.?(\d*)", n)
            major = int(match.group(1)) if match else 0
            minor = int(match.group(2)) if match and match.group(2) else 0
            return (major, minor)

        flash_models = sorted(
            [n for n in names if "flash" in n and "lite" not in n],
            key=version_key, reverse=True,
        )
        best = flash_models[0] if flash_models else (
            sorted([n for n in names if n.startswith("gemini")], key=version_key, reverse=True)[:1] or [None]
        )[0]

        if best:
            _GEMINI_MODEL_CACHE["model"]      = best
            _GEMINI_MODEL_CACHE["checked_at"] = now
            logger.info(f"Gemini model auto-discovered: {best}")
            return best
    except Exception as e:
        logger.warning(f"Gemini model discovery failed, will use fallback list: {e}")

    return None


def _call_gemini_model(model: str, key: str, messages: list, system_prompt: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    contents = [
        {"role": "model" if m["role"] == "assistant" else "user",
         "parts": [{"text": m["content"]}]}
        for m in messages
    ]
    payload = {
        "contents"         : contents,
        "systemInstruction": {"parts": [{"text": system_prompt}]},
    }
    headers = {"Content-Type": "application/json", "x-goog-api-key": key}
    resp = _post_json(url, payload, headers)
    return resp["candidates"][0]["content"]["parts"][0]["text"]


def chat_gemini(messages: list, system_prompt: str) -> dict:
    key = os.getenv("GEMINI_API_KEY", "")
    if not key:
        return {"success": False, "error": "GEMINI_API_KEY not set"}

    manual_override = os.getenv("GEMINI_MODEL", "")
    candidates: list = []
    if manual_override:
        candidates.append(manual_override)

    discovered = _discover_gemini_model(key)
    if discovered and discovered not in candidates:
        candidates.append(discovered)

    for name in _GEMINI_FALLBACK_CANDIDATES:
        if name not in candidates:
            candidates.append(name)

    last_error = None
    for model in candidates:
        try:
            text = _call_gemini_model(model, key, messages, system_prompt)
            # Success — remember this model so we skip the failed ones next time
            _GEMINI_MODEL_CACHE["model"]      = model
            _GEMINI_MODEL_CACHE["checked_at"] = time.time()
            return {"success": True, "text": text, "provider": f"gemini ({model})"}
        except Exception as e:
            last_error = str(e)
            logger.warning(f"Gemini model '{model}' failed ({e}), trying next candidate...")
            continue

    logger.error(f"All Gemini model candidates failed: {last_error}")
    return {"success": False, "error": last_error or "All Gemini models unavailable"}


PROVIDERS = {"claude": chat_claude, "openai": chat_openai, "gemini": chat_gemini}


def chat(messages: list, system_prompt: str, provider: str = None) -> dict:
    """
    Try providers in priority order: explicit `provider` > AI_PROVIDER env >
    Claude -> OpenAI -> Gemini (first one with a valid key + successful call wins).
    """
    if provider and provider in PROVIDERS:
        order = [provider, "gemini"] if provider != "gemini" else ["gemini"]
    else:
        configured = os.getenv("AI_PROVIDER", "").lower()
        order = [configured] if configured in PROVIDERS else ["claude", "openai", "gemini"]

    last_error = None
    for name in order:
        result = PROVIDERS[name](messages, system_prompt)
        if result.get("success"):
            return result
        last_error = result.get("error")

    return {"success": False, "error": last_error or "No AI provider configured"}
