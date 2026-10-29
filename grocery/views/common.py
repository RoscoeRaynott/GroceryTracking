import streamlit as st


def go(page):
    st.session_state["page"] = page
    st.rerun()


def back_home():
    if st.button("Home", icon=":material/arrow_back:", type="tertiary", key="back_home"):
        go("home")


def coming_soon(title, phase, what):
    back_home()
    st.title(title)
    st.info(f"Coming in **Phase {phase}**: {what}")
