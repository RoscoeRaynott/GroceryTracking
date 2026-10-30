import streamlit as st

from grocery.views import add_purchase, common, home, needs_info, settings, voice_update

st.set_page_config(page_title="Grocery Tracker", page_icon="🛒", layout="centered")

PAGES = {
    "home": home.render,
    "settings": settings.render,
    "add": add_purchase.render,
    "needs_info": needs_info.render,
    "voice": voice_update.render,
    "pantry": lambda: common.coming_soon(
        "Pantry", 4, "everything open, grouped by freezer, fridge and open air, with warnings."),
    "insights": lambda: common.coming_soon(
        "Insights", 4, "best place to buy, lifespan charts, and later nutrition."),
}

PAGES.get(st.session_state.get("page", "home"), home.render)()
