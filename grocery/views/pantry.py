"""Pantry: everything still open as a photo grid, food by where it's kept and household items apart.

The "Needs info" view (it used to be its own page) shows only items with something still to fill in;
tapping any tile opens the item panel, which has every tool to fill it.
"""

import base64
import hashlib
import html
import pathlib
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

    if not st.session_state.get("pictures_tried"):
        # Missing pictures come from Wikipedia, which can be slow: try once per visit, never on every tap.
        st.session_state["pictures_tried"] = True
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
    scans = st.session_state.get(item_info.SCANS, {})
    scanned = [r for r in rows if r["product"] and r["product"]["product_id"] in scans]
    if scans:
        photos = sum(len(q.get("photos", [])) for q in scans.values())
        st.markdown(f":blue-badge[📷 {len(scans)} item{'s' if len(scans) != 1 else ''} scanned"
                    + (f" · {photos} photo{'s' if photos != 1 else ''}" if photos else "") + "] :gray[waiting for a lookup]")
    if view != "All":
        if not needs and not scanned:
            st.success("Everything has its info. 🎉")
        lookable = _look_up_all(needs + [r for r in scanned if r not in needs])
    elif scanned:
        lookable = _look_up_all(scanned, label="🔎 Look up scanned")
    else:
        lookable = set()
    st.caption("Tap an item to see everything about it, fix it, mark it finished or say some went bad.")
    st.markdown(TILE_CSS, unsafe_allow_html=True)
    st.html(TAP_JS, unsafe_allow_javascript=True)

    food = [r for r in shown if r["item"].get("kind", "Food") == "Food"]
    groups = [(f"{place}", [r for r in food if (r["item"].get("storage") or "Open air") == place]) for place in PLACES]
    groups.append(("🧴 Household", [r for r in shown if r["item"].get("kind", "Food") != "Food"]))
    for title, here in groups:
        if not here:
            continue
        here.sort(key=lambda r: ({"red": 0, "orange": 1, "ok": 2}[r["level"]], -r["days_open"]))
        st.subheader(f"{title} · {len(here)}", anchor=False)
        with st.container(horizontal=True, gap="small", key=f"grid_{title.encode('ascii', 'ignore').decode().strip().replace(' ', '_') or 'household'}"):
            for r in here:  # a tick box only on items a Look up button will act on
                _tile(r, (r["product"] or {}).get("product_id") in lookable)
    item_info.show_if_open()


# Every tile is the same size: its picture, name and badges are drawn as one fixed-size block, so tall and
# wide photos line up. The whole tile is one invisible button (tap anywhere on it to open the item); the tick
# box in "Needs info" sits above it.
TILE_CSS = """<style>
[class*="st-key-tile_"] { position: relative; padding: 6px 4px 8px; transition: transform .08s, background .08s, opacity .15s;
  -webkit-tap-highlight-color: transparent; }
/* Instant feedback: the tile presses in the moment it's touched */
[class*="st-key-tile_"]:active, [class*="st-key-tile_"]:has(button:active) { transform: scale(.94); background: rgba(255,75,75,.18); }
/* While the app is working, tiles dim and ignore taps, so an extra tap can't get lost or close the panel */
.stApp[data-test-script-state="running"] [class*="st-key-tile_"] { opacity: .5; }
.stApp[data-test-script-state="running"] [class*="st-key-pi_"] button,
.stApp[data-test-script-state="running"] [class*="st-key-ni_pick_"] { pointer-events: none; }
[class*="st-key-tile_"].gt-pressed { transform: scale(.94); background: rgba(255,75,75,.18); opacity: 1 !important; }
[class*="st-key-tile_"] [data-testid="stMarkdown"], [class*="st-key-tile_"] [data-testid="stMarkdownContainer"],
[class*="st-key-tile_"] [data-testid="stMarkdownContainer"] p { margin: 0 !important; }
[class*="st-key-pi_"] { position: absolute !important; inset: 0 !important; width: auto !important; z-index: 5; margin: 0; }
[class*="st-key-pi_"] > div, [class*="st-key-pi_"] button { width: 100% !important; height: 100% !important; }
[class*="st-key-pi_"] button { opacity: 0; touch-action: manipulation; }
[class*="st-key-ni_pick_"] { position: absolute; top: 2px; left: 4px; z-index: 6; }
</style>"""
# Smoother taps (installed once per page load):
# - the tile you tapped stays pressed until the app has answered, so you know it heard you;
# - for 0.8 s after the item panel opens, taps outside it are ignored, so a second tap on the tile
#   can't close the panel it just opened.
TAP_JS = """<script>
(() => {
  if (window.__gtTaps) return; window.__gtTaps = true;
  const doc = window.parent && window.parent.document ? window.parent.document : document;
  let openedAt = 0, hadDialog = false;
  new MutationObserver(() => {
    const has = !!doc.querySelector('[role="dialog"]');
    if (has && !hadDialog) openedAt = Date.now();
    hadDialog = has;
    const app = doc.querySelector('.stApp');
    if (app && app.getAttribute('data-test-script-state') !== 'running')
      doc.querySelectorAll('.gt-pressed').forEach(t => t.classList.remove('gt-pressed'));
  }).observe(doc.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['data-test-script-state'] });
  const guard = e => {
    if (Date.now() - openedAt < 800 && !(e.target.closest && e.target.closest('[role="dialog"]'))) {
      e.stopPropagation(); e.preventDefault();
    }
  };
  ['pointerdown', 'mousedown', 'touchstart', 'click', 'pointerup', 'mouseup', 'touchend'].forEach(
    type => doc.addEventListener(type, guard, { capture: true, passive: false }));
  doc.addEventListener('pointerdown', e => {
    const tile = e.target.closest && e.target.closest('[class*="st-key-tile_"]');
    if (tile) tile.classList.add('gt-pressed');
  }, true);
})();
</script>"""
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


STATIC = pathlib.Path(__file__).resolve().parents[2] / "static" / "pics"


def picture_file(url):
    """A saved picture (a data URI) as a small file the browser keeps, so a tap doesn't resend every tile's
    picture. Falls back to the data URI itself if the file can't be written."""
    if not str(url).startswith("data:image/"):
        return url
    try:
        head, data = url.split(",", 1)
        ext = "png" if "png" in head else "webp" if "webp" in head else "jpg"
        name = f"{hashlib.sha1(data.encode()).hexdigest()[:16]}.{ext}"
        path = STATIC / name
        if not path.exists():
            STATIC.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(base64.b64decode(data))
            tmp.replace(path)
        return f"app/static/pics/{name}"
    except Exception:
        return url


def _tile_html(r):
    b, item, product = r["batch"], r["item"], r["product"]
    url = (product or {}).get("photo_url") or (item.get("photo_url") if item.get("photo_url") not in ("", "none") else "")
    url = picture_file(url)
    picture = (f'<img src="{html.escape(url)}" style="width:70px;height:70px;object-fit:contain">' if url else
               f'<span style="font-size:46px;line-height:70px">{item.get("emoji") or "🛒"}</span>')
    bg, fg = COLORS[BADGE[r["level"]]]
    warn = "⚠️ " if r["level"] == "red" else ""
    pid = b.get("product_id")
    found = st.session_state.get(f"ii_redo_{pid}") if pid else None
    queued = st.session_state.get(item_info.SCANS, {}).get(pid)
    if found and not found.get("missing"):
        ready = "✅" + item_info.VIA.get(found.get("via"), "") + " "  # found, and how
    elif queued:
        ready = ("📷" if queued.get("photos") else "") + ("🔢" if queued.get("barcode") else "") + " "  # waiting
    else:
        ready = ""
    checks = r["checks"]
    pct = round(100 * sum(1 for _, ok in checks if ok) / len(checks)) if checks else 0
    bar_color = COLORS["red" if pct < 40 else "orange" if pct < 80 else "green"][1]
    filled = round(pct / 20)
    return f"""<div style="display:flex;flex-direction:column;align-items:center;height:104px;justify-content:space-between">
<div style="height:70px;display:flex;align-items:center;justify-content:center">{picture}</div>
<div style="font-size:13px;font-weight:600;line-height:18px;padding-bottom:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:104px">{html.escape(records.short_name(product, item))}</div>
<div style="font-size:12px;white-space:nowrap">{ready}<span style="background:{bg};color:{fg};border-radius:6px;padding:1px 6px">{warn}{r["days_open"]}d</span>
<span style="color:{bar_color};letter-spacing:-1px">{"▰" * filled}{"▱" * (5 - filled)}</span></div></div>"""


def _look_up_all(needs, label="🔎 Look up all"):
    """Look up every ticked item at once (up to 10 at a time), each the cheapest way that fills what's missing:
    its waiting photos (read by the cheap label model), else its barcode (the free database first), else one
    web search by name. Items a lookup can't help (only a photo, kept in or expiry missing; loose food, whose
    nutrition comes from USDA) and items not found in the last 30 days are left out unless something new was
    scanned for them. Each match waits on its tile (✅) and in its panel for Yes / Not this."""
    scans = st.session_state.get(item_info.SCANS, {})
    todo, seen, skipped, lookable = [], set(), {"nothing": 0, "missed": 0}, set()
    for r in needs:
        p = r["product"]
        if not p or p["product_id"] in seen or r["item"].get("kind", "Food") == "One-time" \
                or st.session_state.get(f"ii_redo_{p['product_id']}"):
            continue
        seen.add(p["product_id"])
        scanned = p["product_id"] in scans
        r["lookup_needs"] = records.lookup_needs(p, r["item"])
        if not scanned and not (r["lookup_needs"] - {"photo"}):
            skipped["nothing"] += 1  # a photo alone: 🖼 in the item finds one from free sources first
            continue
        if not scanned and records.missed_recently(p):
            skipped["missed"] += 1
            continue
        lookable.add(p["product_id"])
        if st.session_state.get(f"ni_pick_{p['product_id']}", True):
            todo.append(r)
    free = sum(1 for r in todo if r["product"]["product_id"] in scans or r["product"].get("barcode"))
    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        go = st.button(f"{label} ({len(todo)})", key="ni_all", type="primary", disabled=not todo,
                       help="Each item is looked up the cheapest way: photo, then barcode, then name. "
                            "Up to 10 at once. Nothing is saved until you say Yes.")
        if st.button("Tick all", key="ni_tickall", icon=":material/check_box:"):
            for pid in lookable:
                st.session_state[f"ni_pick_{pid}"] = True
            st.rerun()
        if st.button("Untick all", key="ni_untickall", icon=":material/check_box_outline_blank:"):
            for pid in lookable:
                st.session_state[f"ni_pick_{pid}"] = False
            st.rerun()
    notes = ([f"📷🔢 {free} by photo / barcode (cheapest)"] if free else []) + \
            ([f"{skipped['nothing']} need only a photo or a date"] if skipped["nothing"] else []) + \
            ([f"{skipped['missed']} not found in the last {records.MISS_DAYS} days (scan its barcode or label to retry)"]
             if skipped["missed"] else [])
    if notes:
        st.caption(" · ".join(notes))
    if not go:
        return lookable
    settings = sheets.get_settings()
    jobs = {}
    for r in todo:  # everything a lookup needs is gathered here, so the parallel lookups don't touch the session
        p = r["product"]
        q = scans.get(p["product_id"], {})
        jobs[p["product_id"]] = (records.product_search_text(p), p.get("pack_unit") or r["item"].get("unit") or "each",
                                 records.lookup_store(p), q.get("barcode") or p.get("barcode", ""), tuple(q.get("photos", ())),
                                 frozenset(r["lookup_needs"]) or None)
    ways = [("📷" if args[4] else "🔢" if args[3] else "🔤") for args in jobs.values()]
    plan = " · ".join(f"{icon} {ways.count(icon)}" for icon in ("🔢", "📷", "🔤") if ways.count(icon))
    bar = st.progress(0.0, text=f"🔎 0 of {len(jobs)} looked up ({plan})")
    ctx = get_script_run_ctx()
    found = missed = 0
    with ThreadPoolExecutor(max_workers=MAX_AT_ONCE, initializer=lambda: add_script_run_ctx(ctx=ctx)) as pool:
        running = {pool.submit(productinfo.best_lookup, settings["model_lookup"], settings["model_labels"], *args): pid
                   for pid, args in jobs.items()}
        for n, job in enumerate(as_completed(running), start=1):
            pid = running[job]
            try:
                st.session_state[f"ii_redo_{pid}"] = job.result()
                found += 1
            except productinfo.NotFound as ex:
                st.session_state[f"ii_redo_{pid}"] = {"missing": str(ex) if " " in str(ex) else "Not found."}
                records.set_lookup_missed(pid)  # don't pay to search for it again for a while
                missed += 1
            except Exception as ex:
                st.session_state[f"ii_redo_{pid}"] = {"missing": f"Search failed: {ex}"}
                missed += 1
            scans.pop(pid, None)  # its photos have been read; the barcode is already saved on the product
            bar.progress(n / len(jobs), text=f"🔎 {n} of {len(jobs)} looked up ({plan}) · ✅ {found} found · ⚠️ {missed} not found")
    st.toast(f"✅ {found} found — tap each ✅ item to check and save")
    st.rerun()
