import streamlit as st

from grocery import records, sheets
from grocery.views.common import go

BIG_BUTTONS_CSS = """
<style>
.st-key-home_nav button { min-height: 72px; justify-content: flex-start; padding-left: 20px; }
.st-key-home_nav button p { font-size: 20px; font-weight: 600; }
</style>
"""


def render():
    st.title("Grocery Tracker")

    _, err = sheets.connect()
    if err:
        st.error("Not connected to your Google Sheet yet. Open **Settings** to see why.")

    # Reminders appear here once Phase 4 adds lifespan and expiry tracking.
    if not err:
        missing = len(records.products_needing_info())
        if missing:
            label = f"{missing} food product{'s' if missing != 1 else ''} need{'s' if missing == 1 else ''} info"
            if st.button(label, icon=":material/info:", type="tertiary"):
                go("needs_info")

    st.markdown(BIG_BUTTONS_CSS, unsafe_allow_html=True)
    with st.container(key="home_nav", gap="medium"):
        if st.button("Add purchase", icon=":material/photo_camera:", type="primary", width="stretch"):
            go("add")
        if st.button("Voice update", icon=":material/mic:", width="stretch"):
            go("voice")
        if st.button("Pantry", icon=":material/kitchen:", width="stretch"):
            go("pantry")
        if st.button("Insights", icon=":material/bar_chart:", width="stretch"):
            go("insights")

    st.write("")
    with st.container(horizontal=True):
        if st.button("History & undo", icon=":material/history:", type="tertiary"):
            go("history")
        if st.button("Settings", icon=":material/settings:", type="tertiary"):
            go("settings")
