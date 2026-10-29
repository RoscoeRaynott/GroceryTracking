"""Gemini API over REST, same pattern as Bookshelf Scanner."""

import json
import urllib.error
import urllib.request

import streamlit as st

API = "https://generativelanguage.googleapis.com/v1beta"


KEY_NAMES = {"gemini_api_key", "google_api_key", "gemini_key", "aistudio_api_key"}


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
