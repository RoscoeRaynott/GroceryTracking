import streamlit as st

from grocery import costs
from grocery.views import add_purchase, costs as costs_view, history, home, insights, lab, pantry, settings, voice_update

st.set_page_config(page_title="Grocery Tracker", page_icon="🛒", layout="centered")

PAGES = {
    "home": home.render,
    "settings": settings.render,
    "add": add_purchase.render,
    "voice": voice_update.render,
    "history": history.render,
    "pantry": pantry.render,
    "insights": insights.render,
    "lab": lab.render,
    "costs": costs_view.render,
}

costs.toast_pending()  # what the last action's AI calls cost (an action that redraws the page shows it here)
PAGES.get(st.session_state.get("page", "home"), home.render)()
costs.toast_pending()  # …or here, when the AI ran in this same run
