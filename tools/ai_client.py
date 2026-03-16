"""
QA AI Automation — Unified AI Client
Abstracts AI provider calls so tools work with either Gemini (free) or Claude.
Uses REST API for Gemini (avoids gRPC quota issues) with auto-fallback to Claude.
"""

import json
import base64
import requests as http_requests
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config


# ═════════════════════════════════════════════════
# Provider Detection
# ═════════════════════════════════════════════════

def _get_provider() -> str:
    """Detect which AI provider to use based on available keys."""
    if config.GEMINI_API_KEY:
        return "gemini"
    if config.ANTHROPIC_API_KEY:
        return "claude"
    raise RuntimeError(
        "No AI provider configured. Set GEMINI_API_KEY (free) or ANTHROPIC_API_KEY in .env"
    )


# ═════════════════════════════════════════════════
# Gemini REST API (avoids gRPC/quota issues)
# ═════════════════════════════════════════════════

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


def _gemini_chat(system_prompt: str, user_message: str) -> str:
    """Send a text chat to Gemini via REST API."""
    url = f"{GEMINI_BASE}/models/{config.GEMINI_MODEL}:generateContent?key={config.GEMINI_API_KEY}"

    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"parts": [{"text": user_message}]}],
        "generationConfig": {
            "maxOutputTokens": config.AI_MAX_TOKENS,
            "temperature": 0.2,
        },
    }

    r = http_requests.post(url, json=payload, timeout=120)
    r.raise_for_status()

    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def _gemini_chat_with_images(
    system_prompt: str,
    user_message: str,
    image_bytes_list: list,
) -> str:
    """Send a vision chat to Gemini via REST API with images."""
    url = f"{GEMINI_BASE}/models/{config.GEMINI_MODEL}:generateContent?key={config.GEMINI_API_KEY}"

    parts = [{"text": system_prompt + "\n\n" + user_message}]
    for img_bytes in image_bytes_list:
        b64 = base64.standard_b64encode(img_bytes).decode("utf-8")
        parts.append({
            "inline_data": {
                "mime_type": "image/png",
                "data": b64,
            }
        })

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "maxOutputTokens": config.AI_MAX_TOKENS,
            "temperature": 0.2,
        },
    }

    r = http_requests.post(url, json=payload, timeout=120)
    r.raise_for_status()

    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


# ═════════════════════════════════════════════════
# Claude Implementation (fallback)
# ═════════════════════════════════════════════════

def _claude_chat(system_prompt: str, user_message: str) -> str:
    """Send a text chat to Claude and return the response text."""
    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=config.AI_MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    return response.content[0].text


def _claude_chat_with_images(
    system_prompt: str,
    user_message: str,
    image_bytes_list: list,
) -> str:
    """Send a vision chat to Claude with images."""
    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    content = [{"type": "text", "text": user_message}]
    for img_bytes in image_bytes_list:
        b64 = base64.standard_b64encode(img_bytes).decode("utf-8")
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": b64,
            },
        })

    response = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=config.AI_MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": content}],
    )
    return response.content[0].text


# ═════════════════════════════════════════════════
# Public API — with automatic fallback
# ═════════════════════════════════════════════════

def ai_chat(system_prompt: str, user_message: str) -> str:
    """Send a text prompt to the configured AI provider.
    Falls back to the other provider if the primary fails.
    """
    provider = _get_provider()

    try:
        if provider == "gemini":
            return _gemini_chat(system_prompt, user_message)
        else:
            return _claude_chat(system_prompt, user_message)
    except Exception as e:
        print(f"   ⚠️  {provider.capitalize()} failed: {e}")
        # Try fallback
        if provider == "gemini" and config.ANTHROPIC_API_KEY:
            print("   ↩️  Falling back to Claude...")
            return _claude_chat(system_prompt, user_message)
        elif provider == "claude" and config.GEMINI_API_KEY:
            print("   ↩️  Falling back to Gemini...")
            return _gemini_chat(system_prompt, user_message)
        raise


def ai_chat_json(system_prompt: str, user_message: str) -> dict:
    """Send a prompt and parse the response as JSON."""
    raw = ai_chat(system_prompt, user_message)

    # Strip markdown code fences if present
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:])
    if text.endswith("```"):
        text = text[:-3]

    return json.loads(text.strip())


def ai_chat_with_images(
    system_prompt: str,
    user_message: str,
    image_bytes_list: list,
) -> str:
    """Send a vision prompt with images. Falls back if primary fails."""
    provider = _get_provider()

    try:
        if provider == "gemini":
            return _gemini_chat_with_images(system_prompt, user_message, image_bytes_list)
        else:
            return _claude_chat_with_images(system_prompt, user_message, image_bytes_list)
    except Exception as e:
        print(f"   ⚠️  {provider.capitalize()} failed: {e}")
        if provider == "gemini" and config.ANTHROPIC_API_KEY:
            print("   ↩️  Falling back to Claude...")
            return _claude_chat_with_images(system_prompt, user_message, image_bytes_list)
        elif provider == "claude" and config.GEMINI_API_KEY:
            print("   ↩️  Falling back to Gemini...")
            return _gemini_chat_with_images(system_prompt, user_message, image_bytes_list)
        raise


def ai_chat_with_images_json(
    system_prompt: str,
    user_message: str,
    image_bytes_list: list,
) -> dict:
    """Send a vision prompt and parse the response as JSON."""
    raw = ai_chat_with_images(system_prompt, user_message, image_bytes_list)

    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:])
    if text.endswith("```"):
        text = text[:-3]

    return json.loads(text.strip())


def get_provider_name() -> str:
    """Return the name of the active AI provider."""
    return _get_provider().capitalize()
