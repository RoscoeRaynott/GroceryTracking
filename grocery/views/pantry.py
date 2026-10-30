"""Pantry: everything still open, grouped by where it's kept, colour-coded, with quick buttons."""

import streamlit as st

from grocery import analytics, events, records, voice
from grocery.gemini import GeminiError
from grocery.views import product_ui
from grocery.views.common import back_home

PLACES = [("Freezer", ":material/ac_unit:"), ("Fridge", ":material/kitchen:"), ("Open air", ":material/shelves:")]
BADGE = {"red": "red", "orange": "orange", "ok": "green"}


def render():
    back_home()
    st.title("Pantry")
    rows = analytics.pantry()
    if not rows:
        st.info("Nothing open. Add a purchase to fill your pantry.")
        return

    products = {p["product_id"]: p for p in records.products()}
    for place, icon in PLACES:
        here = [r for r in rows if (r["item"].get("storage") or "Open air") == place]
        if not here:
            continue
        here.sort(key=lambda r: ({"red": 0, "orange": 1, "ok": 2}[r["level"]], -r["days_open"]))
        st.subheader(f"{place} · {len(here)}", anchor=False)
        for r in here:
            _row(r, products.get(r["batch"].get("product_id")))


def _row(r, product=None):
    b, item = r["batch"], r["item"]
    bid = b["batch_id"]
    color = BADGE[r["level"]]
    with st.container(border=True):
        with st.container(horizontal=True, vertical_alignment="center"):
            product_ui.photo(product, item.get("emoji", ""), width=48)
            usual = f" / ~{r['expected']:.0f}d" if r["expected"] else ""
            note = f" :{color}-badge[{r['reason']}]" if r["reason"] else ""
            st.markdown(f"**{b['item']}** :{color}-badge[{r['days_open']}d{usual}]{note}  \n"
                        f":gray[{b.get('units', '')} {b.get('unit', '')} · {b.get('store', '')}]")
        with st.container(horizontal=True):
            if st.button("Finished", key=f"pf_{bid}", icon=":material/check:"):
                events.apply([{"type": "finish", "batch": b}], said="Pantry: Finished", source="pantry")
                st.toast(f"{b['item']} finished · Undo in History")
                st.rerun()
            with st.popover("Some went bad", icon=":material/delete:", key=f"pop_{bid}"):
                _went_bad(b)


def _went_bad(b):
    bid = b["batch_id"]
    parsed = st.session_state.get(f"pw_{bid}")
    if parsed is None:
        text = st.text_input("What went bad?", key=f"pwt_{bid}",
                             placeholder="e.g. half · 200 grams · one bell pepper · 2 slices, rest is fine")
        if st.button("Check", key=f"pwc_{bid}", type="primary", disabled=not text.strip()):
            with st.spinner("Reading…"):
                try:
                    st.session_state[f"pw_{bid}"] = dict(voice.parse_waste(b, text), text=text)
                except GeminiError as ex:
                    st.error(f"Couldn't read that: {ex}")
                    return
            st.rerun()
        return

    share = events.fraction_text(float(parsed.get("fraction") or 0))
    done = parsed.get("batch_done", True)
    st.markdown(f":orange-badge[{share} wasted] "
                + (":gray-badge[rest eaten · closes]" if done else ":green-badge[rest still left]"))
    st.caption(parsed.get("summary", ""))
    with st.container(horizontal=True):
        if st.button("Approve", key=f"pwa_{bid}", type="primary"):
            events.apply([{"type": "waste", "batch": b, "fraction": parsed.get("fraction"),
                           "batch_done": done, "happened_on": parsed.get("happened_on")}],
                         said=f"Pantry: {parsed['text']}", source="pantry")
            st.session_state.pop(f"pw_{bid}", None)
            st.toast(f"Saved · Undo in History")
            st.rerun()
        if st.button("Cancel", key=f"pwx_{bid}"):
            st.session_state.pop(f"pw_{bid}", None)
            st.rerun()
