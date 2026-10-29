"""Gemini API over REST, same pattern as Bookshelf Scanner."""

import base64
import json
import time
import urllib.error
import urllib.request

import streamlit as st

from grocery import sheets, timeutil

API = "https://generativelanguage.googleapis.com/v1beta"
KEY_NAMES = {"gemini_api_key", "google_api_key", "gemini_key", "aistudio_api_key"}
RETRY_CODES = {429, 500, 502, 503, 504}


class GeminiError(Exception):
    pass


def api_key():
    """Find the key anywhere in secrets, like Bookshelf Scanner does.

    In TOML, a line written below a [section] header belongs to that section,
    so the key may sit inside [gsheets] or [gcp_service_account].
    """
    def walk(node):
        try:
            items = node.items()
        except Exception:
            return None
        for k, v in items:
            if str(k).lower() in KEY_NAMES and isinstance(v, str) and v.strip():
                return v.strip().strip("\"'")
        for _, v in items:
            found = walk(v)
            if found:
                return found
        return None

    try:
        return walk(st.secrets)
    except Exception:
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def list_models(key):
    """Model ids this key can use for generateContent, or raise on error."""
    req = urllib.request.Request(f"{API}/models?pageSize=1000", headers={"x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return sorted(
        m["name"].removeprefix("models/")
        for m in data.get("models", [])
        if "generateContent" in m.get("supportedGenerationMethods", [])
    )


def generate_json(task, model, prompt, media=()):
    """Send a prompt plus media [(bytes, mime type)] and return the parsed JSON reply.

    Every call is logged to the ai_log tab (model, tokens, seconds).
    """
    key = api_key()
    if not key:
        raise GeminiError("No Gemini API key in Streamlit secrets.")
    parts = [{"text": prompt}]
    for data, mime in media:
        parts.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode("ascii")}})
    body = json.dumps({
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseMimeType": "application/json"},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{API}/models/{model}:generateContent", data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
    )

    started = time.time()
    usage, ok = {}, False
    try:
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=180) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as ex:
                detail = ex.read().decode("utf-8", "replace")[:500]
                if ex.code in RETRY_CODES and attempt == 0:
                    time.sleep(3)
                    continue
                raise GeminiError(f"Gemini {model} returned HTTP {ex.code}: {detail}") from ex
            except urllib.error.URLError as ex:
                raise GeminiError(f"Could not reach Gemini: {ex.reason}") from ex
        usage = data.get("usageMetadata", {})
        text = "".join(
            p.get("text", "")
            for c in data.get("candidates", [])[:1]
            for p in c.get("content", {}).get("parts", [])
        )
        if not text:
            reason = (data.get("candidates") or [{}])[0].get("finishReason") or data.get("promptFeedback")
            raise GeminiError(f"Gemini {model} returned no text ({reason}).")
        result = _parse_json(text)
        ok = True
        return result
    finally:
        _log(task, model, usage, time.time() - started, ok)


def _parse_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    try:
        return json.loads(text)
    except json.JSONDecodeError as ex:
        raise GeminiError(f"Gemini's reply was not valid JSON: {ex}") from ex


def _log(task, model, usage, seconds, ok):
    try:
        sheets.append_rows("ai_log", [{
            "at": timeutil.now_str(),
            "task": task,
            "model": model,
            "input_tokens": usage.get("promptTokenCount", ""),
            "output_tokens": usage.get("candidatesTokenCount", ""),
            "seconds": round(seconds, 1),
            "ok": ok,
        }])
    except Exception:
        pass  # Logging must never break the actual task.
