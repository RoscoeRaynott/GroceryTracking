import streamlit as st

from grocery import gemini, sheets
from grocery.schema import TABS
from grocery.views.common import back_home

MODEL_TASKS = {
    "model_receipts": "Reading receipts",
    "model_voice": "Understanding voice",
    "model_labels": "Reading labels",
}


def render():
    back_home()
    st.title("Settings")

    sh, err = _sheet_status()
    models = _gemini_status()

    if err:
        return
    current = sheets.get_settings()

    with st.form("settings_form", border=True):
        st.subheader("AI model per task")
        chosen = {}
        for key, label in MODEL_TASKS.items():
            if models:
                saved = current.get(key, "")
                index = models.index(saved) if saved in models else _default_model_index(models)
                chosen[key] = st.selectbox(label, models, index=index)
            else:
                chosen[key] = st.text_input(label, value=current.get(key, ""))

        st.subheader("Formula numbers")
        chosen["min_batches_before_warning"] = st.number_input(
            "Finished batches before warnings start", min_value=1, step=1,
            value=int(float(current["min_batches_before_warning"])))
        chosen["warning_multiplier"] = st.number_input(
            "Warn when open longer than × usual lifespan", min_value=1.0, step=0.05,
            value=float(current["warning_multiplier"]))
        chosen["running_average_batches"] = st.number_input(
            "Usual lifespan uses the last … batches", min_value=1, step=1,
            value=int(float(current["running_average_batches"])))
        chosen["expiry_warning_days"] = st.number_input(
            "Expiry warning, days before", min_value=0, step=1,
            value=int(float(current["expiry_warning_days"])))
        chosen["best_buy_min_batches"] = st.number_input(
            "Best place to buy needs … finished batches per option", min_value=1, step=1,
            value=int(float(current["best_buy_min_batches"])))

        if st.form_submit_button("Save settings", type="primary", width="stretch"):
            changed = {k: v for k, v in chosen.items() if str(v) != str(current.get(k, ""))}
            if changed:
                sheets.save_settings(changed)
                st.success(f"Saved {len(changed)} setting(s).")
            else:
                st.info("Nothing changed.")


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
        return []
    try:
        models = gemini.list_models(key)
    except Exception as ex:
        st.error(f"Key found, but listing models failed: {ex}")
        return []
    st.success(f"Key works. {len(models)} models available.")
    return models


def _default_model_index(models):
    # Until a model is saved, suggest the first "flash" model in the list.
    for i, m in enumerate(models):
        if "flash" in m:
            return i
    return 0
