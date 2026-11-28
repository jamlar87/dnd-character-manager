from __future__ import annotations
from pathlib import Path
import json
import os
import urllib.request
from .core import LLM_TIMEOUT


# ═══════════════════════════════════════════════════════════════════════════════
# LLM Callers (sync, urllib — no httpx dependency)
# ═══════════════════════════════════════════════════════════════════════════════

def _load_env_key(name: str) -> str:
    """Read a key from the environment or ~/.hermes/.env (where Hermes keeps them)."""
    key = os.environ.get(name, "")
    if key:
        return key
    env_file = Path(os.path.expanduser("~/.hermes/.env"))
    if env_file.exists():
        for line in env_file.read_text().split("\n"):
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _load_deepseek_key() -> str:
    """Load DeepSeek API key from env or ~/.hermes/.env."""
    return _load_env_key("DEEPSEEK_API_KEY")


def _call_deepseek(prompt: str, model: str = "deepseek-chat") -> str | None:
    """Call DeepSeek API (V4 Pro / Chat)."""
    key = _load_deepseek_key()
    if not key:
        return None
    try:
        body = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 4096,
            "temperature": 0.1,
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            "https://api.deepseek.com/v1/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            }
        )
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
            data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"  [DeepSeek] {e}")
        return None


def _call_gemini(prompt: str) -> str | None:
    """Tier 2: Google Gemini Flash. `gemini-2.0-flash` was retired (404) — 2.5-flash is current."""
    key = _load_env_key("GOOGLE_API_KEY")
    if not key:
        return None
    try:
        body = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 4096}
        }).encode()
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}",
            data=body,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
            result = json.loads(resp.read())
            return result["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        print(f"  [Gemini] {e}")
        return None


def _call_ollama(prompt: str) -> str | None:
    """Tier 3: Local Ollama (gemma4 on Bazzite)."""
    try:
        body = json.dumps({
            "model": os.environ.get("AI_OLLAMA_MODEL", "gemma4-26b-a4b:latest"),
            "prompt": prompt,
            "stream": False,
            "think": False,
            # Sampler params must sit in "options"; top-level copies are ignored by /api/generate.
            "options": {"temperature": 0.1, "num_predict": 4096,
                        "num_ctx": int(os.environ.get("AI_OLLAMA_NUM_CTX", "32768"))}
        }).encode()
        req = urllib.request.Request(
            os.environ.get("OLLAMA_URL", "http://192.168.1.31:11434/api/generate"),
            data=body,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
            result = json.loads(resp.read())
            return result.get("response", "")
    except Exception as e:
        print(f"  [Ollama] {e}")
        return None


def _call_openrouter(prompt: str, model: str | None = None) -> str | None:
    """Tier: OpenRouter (free models available). Off by default — see INGEST_LLM_ORDER.

    Measured on a real 7k chunk (MPMM pp.32-37): two of three free models returned HTTP 429
    immediately, and the one that answered took 144s and produced no usable items. Free is
    workable for a page, not for a 3000-chunk library; keep the paid tier first unless a
    specific model has been shown to behave.
    """
    key = _load_env_key("OPENROUTER_API_KEY")
    if not key:
        return None
    model = model or os.environ.get("OPENROUTER_MODEL", "qwen/qwen3.8-27b:free")
    try:
        body = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 4096,
        }).encode()
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}",
                     "X-Title": "DnD Character Manager ingestion"},
        )
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
            result = json.loads(resp.read())
        return result["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"  [OpenRouter] {e}")
        return None


def _call_llm(prompt: str) -> str | None:
    """Tiered LLM chain. Order via INGEST_LLM_ORDER (default deepseek,gemini,ollama).

    Set it to e.g. "gemini,deepseek" to run a sweep on the free tier first, or
    "openrouter,deepseek" to try OpenRouter's free models — both are slower and free tiers
    throw transient 429/503s, so the paid tier stays in the chain as the safety net.
    """
    order = [s.strip() for s in
             os.environ.get("INGEST_LLM_ORDER", "deepseek,gemini,ollama").split(",") if s.strip()]
    tiers = {"deepseek": _call_deepseek, "gemini": _call_gemini,
             "openrouter": _call_openrouter, "ollama": _call_ollama}
    for i, name in enumerate(order):
        fn = tiers.get(name)
        if not fn:
            continue
        if i:
            print(f"  {order[i - 1]} failed, trying {name}...")
        result = fn(prompt)
        if result:
            return result
    return None
