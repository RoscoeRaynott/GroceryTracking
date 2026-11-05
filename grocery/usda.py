"""USDA FoodData Central: typical nutrition for foods without a label (fruit, vegetables, raw meat, fish).

The key lives in Streamlit secrets as USDA_API_KEY and is sent in a header, never in the URL.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

import streamlit as st

API = "https://api.nal.usda.gov/fdc/v1"


def api_key():
    """USDA_API_KEY from Streamlit secrets, wherever it sits (a line under a [section] belongs to that section)."""
    def walk(node):
        try:
            items = node.items()
        except Exception:
            return None
        for k, v in items:
            if str(k).lower() == "usda_api_key" and isinstance(v, str) and v.strip():
                return v.strip()
        for _, v in items:
            found = walk(v)
            if found:
                return found
        return None
    try:
        return walk(st.secrets)
    except Exception:
        return None


def _get(path, params=None):
    key = api_key()
    if not key:
        raise RuntimeError("No USDA_API_KEY in Streamlit secrets.")
    url = f"{API}/{path}" + (f"?{urllib.parse.urlencode(params, doseq=True)}" if params else "")
    req = urllib.request.Request(url, headers={"X-Api-Key": key, "User-Agent": "GroceryTracking/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as ex:
        detail = ex.read().decode("utf-8", "replace")[:300].replace(key, "•••")
        raise RuntimeError(f"USDA returned HTTP {ex.code}: {detail}") from ex


def search(query, page_size=3):
    """Generic foods (not branded) matching the query, best first."""
    return _get("foods/search", {"query": query, "pageSize": page_size,
                                 "dataType": ["Foundation", "SR Legacy"]})


def food(fdc_id):
    """One food in full, including its typical portions (e.g. 1 medium = 118 g)."""
    return _get(f"food/{fdc_id}")
