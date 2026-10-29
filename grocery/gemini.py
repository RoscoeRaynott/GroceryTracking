"""Gemini API over REST, same pattern as Bookshelf Scanner."""

import json
import urllib.error
import urllib.request

import streamlit as st

API = "https://generativelanguage.googleapis.com/v1beta"


def api_key():
    try:
        return str(st.secrets["GEMINI_API_KEY"]).strip().strip("\"'")
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
