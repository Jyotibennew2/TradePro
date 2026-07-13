"""
TradePro Backend - Multi-provider AI Chat Service
Priority: Claude -> OpenAI -> Gemini (free fallback).
Configure via environment variables:
  ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY
  AI_PROVIDER (optional override: "claude" | "openai" | "gemini")
"""

import os
import json
import logging
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)


def _post_json(url: str, payload: dict, headers: dict, timeout: int = 30) -> dict:
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(url, data=data, headers=headers, method="POST")
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


def chat_gemini(messages: list, system_prompt: str) -> dict:
    key = os.getenv("GEMINI_API_KEY", "")
    if not key:
        return {"success": False, "error": "GEMINI_API_KEY not set"}
    model    = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    url      = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    contents = [
        {"role": "model" if m["role"] == "assistant" else "user",
         "parts": [{"text": m["content"]}]}
        for m in messages
    ]
    payload = {
        "contents"         : contents,
        "systemInstruction": {"parts": [{"text": system_prompt}]},
    }
    try:
        resp = _post_json(url, payload, {"Content-Type": "application/json"})
        text = resp["candidates"][0]["content"]["parts"][0]["text"]
        return {"success": True, "text": text, "provider": "gemini"}
    except Exception as e:
        logger.error(f"Gemini chat error: {e}")
        return {"success": False, "error": str(e)}


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
