"""Pantry: everything still open as a photo grid, food by where it's kept and household items apart.

The "Needs info" view (it used to be its own page) shows only items with something still to fill in;
tapping any tile opens the item panel, which has every tool to fill it.
"""

import html
from concurrent.futures import ThreadPoolExecutor, as_completed

import streamlit as st
from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx

from grocery import analytics, productinfo, records, sheets
from grocery.views import item_info, voice_update
from grocery.views.common import back_home

PLACES = ["Freezer", "Fridge", "Open air"]
BADGE = {"red": "red", "orange": "orange", "ok": "green"}
VIEW = "pantry_view"
MAX_AT_ONCE = 10


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
    for r in rows:
        r["product"] = products.get(r["batch"].get("product_id"))
        r["checks"] = records.completeness(r["product"], r["item"], r["batch"])
        # Expiry alone doesn't count as needing info: most dates are long enough to not bother.
        r["needs"] = any(not ok for what, ok in r["checks"] if what != "expiry")

    needs = [r for r in rows if r["needs"]]
    if st.session_state.get(VIEW) not in ("All", "Needs info"):
        st.session_state[VIEW] = "All"  # Home's "N items need info" link sets "Needs info"
    view = st.segmented_control("Show", ["All", "Needs info"], key=VIEW, label_visibility="collapsed",
                                format_func=lambda v: v if v == "All" else f"⚠️ Needs info ({len(needs)})") or "All"
    shown = rows if view == "All" else needs
    if view != "All":
        if not needs:
            st.success("Everything has its info. 🎉")
        _look_up_all(needs)
    st.caption("Tap an item to see everything about it, fix it, mark it finished or say some went bad.")
    st.markdown(TILE_CSS, unsafe_allow_html=True)

    food = [r for r in shown if r["item"].get("kind", "Food") == "Food"]
    groups = [(f"{place}", [r for r in food if (r["item"].get("storage") or "Open air") == place]) for place in PLACES]
    groups.append(("🧴 Household", [r for r in shown if r["item"].get("kind", "Food") != "Food"]))
    picking = view != "All"
    for title, here in groups:
        if not here:
            continue
        here.sort(key=lambda r: ({"red": 0, "orange": 1, "ok": 2}[r["level"]], -r["days_open"]))
        st.subheader(f"{title} · {len(here)}", anchor=False)
        with st.container(horizontal=True, gap="small", key=f"grid_{title.encode('ascii', 'ignore').decode().strip().replace(' ', '_') or 'household'}"):
            for r in here:
                _tile(r, picking)
    item_info.show_if_open()


# Every tile is the same size: its picture, name and badges are drawn as one fixed-size block, so tall and
# wide photos line up. The whole tile is one invisible button (tap anywhere on it to open the item); the tick
# box in "Needs info" sits above it.
TILE_CSS = """<style>
[class*="st-key-tile_"] { position: relative; padding: 6px 4px 8px; }
[class*="st-key-tile_"] [data-testid="stMarkdown"], [class*="st-key-tile_"] [data-testid="stMarkdownContainer"],
[class*="st-key-tile_"] [data-testid="stMarkdownContainer"] p { margin: 0 !important; }
[class*="st-key-pi_"] { position: absolute; inset: 0; z-index: 5; margin: 0; }
[class*="st-key-pi_"] button { width: 100%; height: 100%; opacity: 0; }
[class*="st-key-ni_pick_"] { position: absolute; top: 2px; left: 4px; z-index: 6; }
</style>"""
COLORS = {"green": ("#21C35433", "#3DD56D"), "orange": ("#FFA42133", "#FFBD45"), "red": ("#FF4B4B33", "#FF6C6C")}


def _tile(r, picking):
    """One square per purchase: picture, name, days badge and a small how-complete bar. Tap to open it."""
    b, item, product = r["batch"], r["item"], r["product"]
    bid = b["batch_id"]
    with st.container(border=True, width=112, gap=None, key=f"tile_{bid}"):
        st.markdown(_tile_html(r), unsafe_allow_html=True)
        if st.button(b["item"], key=f"pi_{bid}", help=b.get("product") or b["item"]):
            item_info.open_panel(bid)
        pid = b.get("product_id")
        if picking and pid:
            st.session_state.setdefault(f"ni_pick_{pid}", True)
            st.checkbox("Include in Look up all", key=f"ni_pick_{pid}", label_visibility="collapsed")


def _tile_html(r):
    b, item, product = r["batch"], r["item"], r["product"]
    url = (product or {}).get("photo_url") or (item.get("photo_url") if item.get("photo_url") not in ("", "none") else "")
    picture = (f'<img src="{html.escape(url)}" style="width:70px;height:70px;object-fit:contain">' if url else
               f'<span style="font-size:46px;line-height:70px">{item.get("emoji") or "🛒"}</span>')
    bg, fg = COLORS[BADGE[r["level"]]]
    warn = "⚠️ " if r["level"] == "red" else ""
    pid = b.get("product_id")
    found = st.session_state.get(f"ii_redo_{pid}") if pid else None
    ready = "✅ " if found and not found.get("missing") else ""
    checks = r["checks"]
    pct = round(100 * sum(1 for _, ok in checks if ok) / len(checks)) if checks else 0
    bar_color = COLORS["red" if pct < 40 else "orange" if pct < 80 else "green"][1]
    filled = round(pct / 20)
    return f"""<div style="display:flex;flex-direction:column;align-items:center;height:104px;justify-content:space-between">
<div style="height:70px;display:flex;align-items:center;justify-content:center">{picture}</div>
<div style="font-size:13px;font-weight:600;line-height:18px;padding-bottom:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:104px">{html.escape(b["item"])}</div>
<div style="font-size:12px;white-space:nowrap">{ready}<span style="background:{bg};color:{fg};border-radius:6px;padding:1px 6px">{warn}{r["days_open"]}d</span>
<span style="color:{bar_color};letter-spacing:-1px">{"▰" * filled}{"▱" * (5 - filled)}</span></div></div>"""


def _look_up_all(needs):
    """Search every ticked product still missing something, up to 10 at a time. Each match waits in that
    item's panel (✅ on its tile) for Yes / Not this; nothing is saved without you."""
    todo, seen = [], set()
    for r in needs:
        p = r["product"]
        if p and p["product_id"] not in seen and r["item"].get("kind", "Food") != "One-time" \
                and st.session_state.get(f"ni_pick_{p['product_id']}", True) \
                and not st.session_state.get(f"ii_redo_{p['product_id']}"):
            seen.add(p["product_id"])
            todo.append(r)
    with st.container(horizontal=True, vertical_alignment="center"):
        go = st.button(f"🔎 Look up all ({len(todo)})", key="ni_all", type="primary", disabled=not todo,
                       help="Untick items whose names need fixing first. Nothing is saved until you say Yes.")
        if st.button("Tick all", key="ni_pick_all", type="tertiary"):
            for r in needs:
                if r["product"]:
                    st.session_state[f"ni_pick_{r['product']['product_id']}"] = True
            st.rerun()
        if st.button("Untick all", key="ni_pick_none", type="tertiary"):
            for r in needs:
                if r["product"]:
                    st.session_state[f"ni_pick_{r['product']['product_id']}"] = False
            st.rerun()
    if not go:
        return
    model = sheets.get_settings()["model_lookup"]
    jobs = {r["product"]["product_id"]: (st.session_state.get(f"ii_q_{r['product']['product_id']}")
                                         or records.product_search_text(r["product"]),
                                         r["product"].get("pack_unit") or r["item"].get("unit") or "each",
                                         records.lookup_store(r["product"])) for r in todo}
    bar = st.progress(0.0, text=f"🔎 0 of {len(jobs)} searched")
    ctx = get_script_run_ctx()
    found = missed = 0
    with ThreadPoolExecutor(max_workers=MAX_AT_ONCE, initializer=lambda: add_script_run_ctx(ctx=ctx)) as pool:
        running = {pool.submit(productinfo.web_lookup, model, q, unit, store=store): pid
                   for pid, (q, unit, store) in jobs.items()}
        for n, job in enumerate(as_completed(running), start=1):
            pid = running[job]
            try:
                st.session_state[f"ii_redo_{pid}"] = job.result()
                found += 1
            except productinfo.NotFound as ex:
                st.session_state[f"ii_redo_{pid}"] = {"missing": str(ex) if " " in str(ex) else "Not found."}
                missed += 1
            except Exception as ex:
                st.session_state[f"ii_redo_{pid}"] = {"missing": f"Search failed: {ex}"}
                missed += 1
            bar.progress(n / len(jobs), text=f"🔎 {n} of {len(jobs)} searched · ✅ {found} found · ⚠️ {missed} not found")
    st.toast(f"✅ {found} found — tap each ✅ item to check and save")
    st.rerun()
