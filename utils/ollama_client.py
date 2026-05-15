"""
Thin, retry-aware wrapper around the local Ollama API.
All LLM calls in the pipeline go through here — never call requests directly.
"""
import json
import time
from typing import Optional

import requests

from config import OLLAMA_API_URL, OLLAMA_TIMEOUT_EXTRACTOR, OLLAMA_TIMEOUT_VERIFIER
from utils.logger import get_logger

logger = get_logger("ollama_client")

_RETRY_DELAYS = [2, 5, 10]   # seconds between retries


def _post_with_retry(payload: dict, timeout: int) -> Optional[str]:
    for attempt, delay in enumerate(_RETRY_DELAYS, start=1):
        try:
            resp = requests.post(OLLAMA_API_URL, json=payload, timeout=timeout)
            resp.raise_for_status()
            raw = resp.json().get("response", "").strip()
            if raw:
                return raw
            logger.warning("Ollama returned empty response (attempt %d)", attempt)
        except requests.exceptions.Timeout:
            logger.warning("Ollama timeout on attempt %d/%d", attempt, len(_RETRY_DELAYS))
        except requests.exceptions.ConnectionError:
            logger.error("Ollama not reachable — is `ollama serve` running?")
            return None
        except Exception as exc:
            logger.error("Ollama unexpected error: %s", exc)
        if attempt < len(_RETRY_DELAYS):
            time.sleep(delay)
    return None


def call_extractor(model: str, prompt: str) -> Optional[str]:
    """Call the extractor/formatter model (text-only prompt)."""
    logger.debug("Calling extractor model=%s prompt_len=%d", model, len(prompt))
    payload = {"model": model, "prompt": prompt, "stream": False, "format": "json"}
    return _post_with_retry(payload, OLLAMA_TIMEOUT_EXTRACTOR)


def call_verifier(model: str, prompt: str) -> Optional[str]:
    """Call the verifier model (text-only prompt)."""
    logger.debug("Calling verifier model=%s prompt_len=%d", model, len(prompt))
    payload = {"model": model, "prompt": prompt, "stream": False}
    return _post_with_retry(payload, OLLAMA_TIMEOUT_VERIFIER)


def parse_json_from_response(text: str) -> Optional[dict]:
    """Extract the first valid JSON object from a model response string."""
    if not text:
        return None
    start = text.find("{")
    end   = text.rfind("}") + 1
    if start == -1 or end == 0:
        logger.warning("No JSON object found in LLM response")
        return None
    try:
        return json.loads(text[start:end])
    except json.JSONDecodeError as exc:
        logger.warning("JSON parse error: %s | snippet: %s", exc, text[start:start+200])
        return None


def is_ollama_alive() -> bool:
    """Quick health-check — returns True if Ollama server responds."""
    try:
        r = requests.get(OLLAMA_API_URL.replace("/api/generate", "/"), timeout=5)
        return r.status_code < 500
    except Exception:
        return False
