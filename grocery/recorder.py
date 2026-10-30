"""A big mic button: tap to start, tap the red stop to finish, or press and hold."""

import base64
import os

import streamlit as st
import streamlit.components.v1 as components

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recorder")
_recorder = components.declare_component("voice_recorder", path=_DIR)


def record(key, prompt="Tap to speak"):
    """Show the mic button. Returns (wav bytes, "audio/wav") once per new recording, else None."""
    value = _recorder(prompt=prompt, key=key)
    if not isinstance(value, dict) or value.get("id") == st.session_state.get(f"{key}_last"):
        return None
    st.session_state[f"{key}_last"] = value.get("id")
    try:
        data = base64.b64decode(str(value.get("data", "")).split(",", 1)[-1])
    except ValueError:
        return None
    return (data, "audio/wav") if len(data) > 44 else None
