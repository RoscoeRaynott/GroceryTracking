"""Pantry: everything still open, grouped by where it's kept, colour-coded, with quick buttons."""

import streamlit as st

from grocery import analytics, records
from grocery.views import item_info, product_ui, voice_update
from grocery.views.common import back_home

PLACES = [("Freezer", ":material/ac_unit:"), ("Fridge", ":material/kitchen:"), ("Open air", ":material/shelves:")]
BADGE = {"red": "red", "orange": "orange", "ok": "green"}


def render():
    back_home()
    st.title("Pantry")
    busy = bool(st.session_state.get("pv"))  # a heard update waiting for approval keeps it open
    with st.expander("🎤 Remember something? :gray[three eggs broke · butter finished…]", expanded=bool(busy)):
        voice_update.panel("pv", "Tap and say what went bad or finished")
    rows = analytics.pantry()
    if not rows:
        st.info("Nothing open. Add a purchase to fill your pantry.")
        return

    records.fill_missing_item_pictures()
    products = {p["product_id"]: p for p in records.products()}
    st.caption("Tap a name to see everything about it, mark it finished, or say some went bad.")
    st.markdown(TILE_CSS, unsafe_allow_html=True)
    for place, icon in PLACES:
        here = [r for r in rows if (r["item"].get("storage") or "Open air") == place]
        if not here:
            continue
        here.sort(key=lambda r: ({"red": 0, "orange": 1, "ok": 2}[r["level"]], -r["days_open"]))
        st.subheader(f"{place} · {len(here)}", anchor=False)
        with st.container(horizontal=True, gap="small", key=f"grid_{place.replace(' ', '_')}"):
            for r in here:
                product = products.get(r["batch"].get("product_id"))
                _tile(r, product, records.completeness(product, r["item"], r["batch"]))
    item_info.show_if_open()


TILE_CSS = """<style>
[class*="st-key-tile_"] { padding: 6px 4px 10px; }
[class*="st-key-tile_"] [data-testid="stMarkdownContainer"] { text-align: center; }
[class*="st-key-tile_"] [data-testid="stImage"] { margin: 0 auto; }
[class*="st-key-tile_"] button p { font-size: 13px; font-weight: 600; line-height: 1.15; }
[class*="st-key-tile_"] [data-testid="stMarkdownContainer"] p { font-size: 12px; line-height: 1.3; margin: 0; }
[class*="st-key-tile_"] > [data-testid="stElementContainer"]:last-child { min-height: 24px; }
</style>"""


def _tile(r, product, checks):
    """One square per purchase: photo, name (tap to open), days badge, and a thin how-complete bar."""
    b, item = r["batch"], r["item"]
    bid = b["batch_id"]
    color = BADGE[r["level"]]
    with st.container(border=True, width=112, gap=None, horizontal_alignment="center", key=f"tile_{bid}"):
        product_ui.photo(product, item.get("emoji", ""), width=72, item=item)
        name = b["item"] if len(b["item"]) <= 22 else b["item"][:20] + "…"
        if st.button(name, key=f"pi_{bid}", type="tertiary", help=b.get("product") or b["item"]):
            item_info.open_panel(bid)
        warn = "⚠️ " if r["level"] == "red" else ""
        st.markdown(f":{color}-badge[{warn}{r['days_open']}d] {item_info.profile_bar(checks, compact=True)}")
