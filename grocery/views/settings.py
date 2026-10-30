import streamlit as st

from grocery import gemini, sheets
from grocery.schema import DEFAULT_SETTINGS, TABS
from grocery.views.common import back_home

MODEL_TASKS = {
    "model_receipts": "Reading receipts",
    "model_voice": "Understanding voice",
    "model_labels": "Reading labels",
    "model_lookup": "Searching the web for products",
}

# (key, label, kind, min, step)
NUMBERS = [
    ("min_batches_before_warning", "Finished batches before warnings start", int, 1, 1),
    ("warning_multiplier", "Warn when open longer than × usual lifespan", float, 1.0, 0.05),
    ("running_average_batches", "Usual lifespan uses the last … batches", int, 1, 1),
    ("expiry_warning_days", "Expiry warning, days before", int, 0, 1),
    ("best_buy_min_batches", "Best place to buy needs … finished batches per option", int, 1, 1),
]


def render():
    back_home()
    st.title("Settings")
    st.caption("Changes save to your Google Sheet as soon as you make them.")

    _, err = _sheet_status()
    models = _gemini_status()
    if err:
        return
    current = sheets.get_settings()

    with st.container(border=True):
        st.subheader("AI model per task")
        for key, label in MODEL_TASKS.items():
            saved = current.get(key) or DEFAULT_SETTINGS[key]
            if models:
                options = models if saved in models else [saved] + models
                st.selectbox(label, options, index=options.index(saved), key=f"set_{key}",
                             on_change=_save, args=(key,))
            else:
                st.text_input(label, value=saved, key=f"set_{key}", on_change=_save, args=(key,))

    with st.container(border=True):
        st.subheader("Formula numbers")
        for key, label, kind, low, step in NUMBERS:
            st.number_input(label, min_value=kind(low), step=kind(step),
                            value=kind(float(current[key])), key=f"set_{key}",
                            on_change=_save, args=(key,))


def _save(key):
    value = st.session_state[f"set_{key}"]
    sheets.save_settings({key: value})
    st.toast(f"Saved: {value}")


def _sheet_status():
    st.subheader("Google Sheet")
    sh, err = sheets.connect()
    if err:
        st.error(f"Not connected. {err}")
        email = sheets.service_account_email()
        if email:
            st.write("Make sure the sheet is shared as **Editor** with:")
            st.code(email, language=None)
        if st.button("Try again"):
            st.rerun()
        return None, err
    st.success(f"Connected to **{sh.title}**. Tabs ready: {', '.join(TABS)}.")
    return sh, None


def _gemini_status():
    st.subheader("Gemini")
    key = gemini.api_key()
    if not key:
        st.error("Missing GEMINI_API_KEY in Streamlit secrets.")
        st.write("Secret names found (values hidden):")
        st.code(", ".join(_secret_names(st.secrets)) or "none", language=None)
        return []
    try:
        models = gemini.list_models(key)
    except Exception as ex:
        st.error(f"Key found, but listing models failed: {ex}")
        return []
    st.success(f"Key works. {len(models)} models available.")
    return models


def _secret_names(node, prefix=""):
    names = []
    try:
        for k, v in node.items():
            path = f"{prefix}{k}"
            if hasattr(v, "items"):
                names.append(f"[{path}]")
                names.extend(_secret_names(v, f"{path}."))
            else:
                names.append(path)
    except Exception:
        pass
    return names
