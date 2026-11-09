import streamlit as st

from grocery import costs
from grocery.views import add_purchase, costs as costs_view, history, home, insights, pantry, settings, voice_update

st.set_page_config(page_title="Grocery Tracker", page_icon="🛒", layout="centered")
# Keyboard hints ("Press Enter to apply", "Ctrl+Enter") mean nothing on a phone: every box has a button instead.
st.markdown("<style>[data-testid='InputInstructions'] { display: none !important; }</style>", unsafe_allow_html=True)

PAGES = {
    "home": home.render,
    "settings": settings.render,
    "add": add_purchase.render,
    "voice": voice_update.render,
    "history": history.render,
    "pantry": pantry.render,
    "insights": insights.render,
    "costs": costs_view.render,
}

costs.toast_pending()  # what the last action's AI calls cost (an action that redraws the page shows it here)
PAGES.get(st.session_state.get("page", "home"), home.render)()
costs.toast_pending()  # …or here, when the AI ran in this same run
