"""In-app camera: live barcode scanning, or quick photos (receipts, labels) without leaving the app."""

import base64
import os

import streamlit as st
import streamlit.components.v1 as components

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "camera")
_camera = components.declare_component("in_app_camera", path=_DIR)


def barcodes(key, label="Scan barcodes"):
    """Live scanner. Returns the newly scanned barcode (digits), once, or None."""
    value = _new(_camera(mode="barcode", label=label, key=key), key)
    code = str(value.get("code", "")).strip() if value else ""
    return code if code.isdigit() else None


def photo(key, label="Open camera"):
    """Camera with a shutter button. Returns {"name", "data": jpeg bytes} once per photo, or None."""
    value = _new(_camera(mode="photo", label=label, key=key), key)
    if not value:
        return None
    try:
        data = base64.b64decode(str(value.get("data", "")).split(",", 1)[-1])
    except ValueError:
        return None
    return {"name": f"camera-{value['id']}.jpg", "data": data} if data else None


def _new(value, key):
    if not isinstance(value, dict) or value.get("id") == st.session_state.get(f"{key}_last"):
        return None
    st.session_state[f"{key}_last"] = value.get("id")
    return value
