import streamlit as st

from grocery.views import add_purchase, history, home, insights, lab, pantry, settings, voice_update

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
}

PAGES.get(st.session_state.get("page", "home"), home.render)()
