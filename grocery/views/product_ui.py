"""Small pieces shown wherever a product appears: its photo and its nicknames."""

import streamlit as st

from grocery import recorder, records, voice
from grocery.gemini import GeminiError


def photo(product, emoji="", width=64, item=None):
    """The product's own photo, else the item's generic picture, else its emoji."""
    url = (product or {}).get("photo_url", "")
    if not url and (item or {}).get("photo_url", "") not in ("", "none"):
        url = item["photo_url"]
    if url:
        st.image(url, width=width)
    else:
        st.markdown(f"<span style='font-size:{int(width * 0.55)}px'>{emoji or '🛒'}</span>",
                    unsafe_allow_html=True)


def nicknames(product, key):
    """Nickname badges with remove buttons, plus a mic and text box to add more."""
    pid = product["product_id"]
    names = records.nicknames(product)
    st.markdown("**Your names for it** " + (" ".join(f":blue-badge[{n}]" for n in names) or ":gray[none yet]"))
    if names:
        with st.container(horizontal=True):
            for n in names:
                if st.button(f"✕ {n}", key=f"{key}_rm_{n}", type="tertiary"):
                    records.remove_nickname(pid, n)
                    st.rerun()

    if not st.session_state.get(f"{key}_open"):
        if st.button("Add a nickname", key=f"{key}_open_btn", icon=":material/mic:", type="tertiary"):
            st.session_state[f"{key}_open"] = True
            st.rerun()
        return
    nonce = st.session_state.setdefault(f"{key}_nn", 0)
    audio = recorder.record(f"{key}_mic_{nonce}", "Tap and say a nickname")
    typed = st.text_input("Nickname", key=f"{key}_txt_{nonce}", label_visibility="collapsed",
                          placeholder="or type: Costco egg whites; egg whites")
    add = st.button("Add nickname", key=f"{key}_add_{nonce}", disabled=not typed.strip())
    new = []
    if audio:
        try:
            new = voice.nicknames_from_audio(audio, product["name"])
        except GeminiError as ex:
            st.error(f"Couldn't hear that: {ex}")
    elif add:
        new = [t for t in typed.split(";")]
    if new:
        records.add_nicknames(pid, new)
        st.session_state[f"{key}_nn"] = nonce + 1
        st.session_state[f"{key}_open"] = False
        st.toast("Nickname saved: " + ", ".join(n.strip() for n in new))
        st.rerun()
