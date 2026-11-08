"""🧪 Thinking trial: read one receipt twice, with the model's normal thinking and with low thinking, and compare
the items and the cost side by side. Nothing is saved to your pantry."""

import difflib
import time

import pandas as pd
import streamlit as st

from grocery import costs, photos, receipts, records, sheets
from grocery.gemini import GeminiError
from grocery.views.common import back_home

RUNS = {"normal": ("🧠 Normal thinking", None), "low": ("⚡ Low thinking", "low")}
STATE = "trial"


def render():
    back_home()
    st.title("🧪 Thinking trial")
    st.caption("Add a long receipt (photos or screenshots). It's read twice: with normal thinking and with low "
               "thinking (cheaper). You see both item lists and what each cost. Nothing is saved to your pantry.")
    state = st.session_state.setdefault(STATE, {})
    photos.pick_photos("trial_photos", "Tap to add receipt photos or screenshots")
    queue = st.session_state.get("trial_photos", [])
    with st.container(horizontal=True):
        go = st.button(f"Run both ({len(queue)} photo{'s' if len(queue) != 1 else ''})", type="primary",
                       disabled=not queue, key="trial_go")
        if st.button("Clear", type="tertiary", key="trial_clear"):
            st.session_state.pop("trial_photos", None)
            st.session_state.pop(STATE, None)
            st.rerun()
    if go:
        state.clear()
        for name, (label, thinking) in RUNS.items():
            with st.spinner(f"{label}: reading {len(queue)} photo{'s' if len(queue) != 1 else ''}…"):
                state[name] = _run(queue, thinking, name)
        st.rerun()
    if state:
        _compare(state)


def _run(queue, thinking, name):
    """The receipt pipeline (read each photo, merge, turn lines into items) at one thinking level."""
    model = sheets.get_settings()["model_receipts"]
    seen, started = len(st.session_state.get(costs.SPEND, [])), time.time()
    try:
        readings = [receipts.transcribe(model, p["data"], thinking=thinking, task=f"trial_{name}_read") for p in queue]
        ordered = receipts.order_photos(readings)
        merged, _ = receipts.merge(ordered)
        info = receipts.summarize(ordered)
        result = receipts.interpret(model, info["store"], info["source_type"], merged, records.items(),
                                    records.products_prompt_lines(), thinking=thinking, task=f"trial_{name}_items")
        error = ""
    except GeminiError as ex:
        result, info, error = {"items": []}, {}, str(ex)
    spent = st.session_state.get(costs.SPEND, [])[seen:]
    return {"items": result.get("items", []), "total": info.get("total_paid"), "error": error,
            "cost": sum(d for _, d, _ in spent), "calls": len(spent), "seconds": time.time() - started}


def _line(it):
    price = it.get("price")
    return f"{it.get('product') or it.get('item') or '?'} · {it.get('units', '')} {it.get('unit', '')} · " + \
        (f"${float(price):.2f}" if isinstance(price, (int, float)) else "price?")


def _compare(state):
    a, b = state.get("normal", {}), state.get("low", {})
    c1, c2 = st.columns(2)
    for col, run, (label, _) in ((c1, a, RUNS["normal"]), (c2, b, RUNS["low"])):
        with col:
            st.markdown(f"**{label}**")
            if run.get("error"):
                st.error(run["error"][:300])
                continue
            priced = sum(float(i["price"]) for i in run["items"] if isinstance(i.get("price"), (int, float)))
            st.metric("Cost", f"${run['cost']:.3f}", help=f"{run['calls']} AI calls · {run['seconds']:.0f} s")
            st.markdown(f":blue-badge[{len(run['items'])} items] :gray-badge[items add up to ${priced:.2f}]"
                        + (f" :gray-badge[receipt total ${float(run['total']):.2f}]" if run.get("total") else ""))
    if a.get("cost") and b.get("cost"):
        st.markdown(f"**Low thinking cost {100 * (1 - b['cost'] / a['cost']):.0f}% less** for this receipt.")

    # Pair items up by name, so differences stand out.
    left, right = list(a.get("items", [])), list(b.get("items", []))
    rows, used = [], set()
    for it in left:
        name = str(it.get("product") or it.get("item") or "").lower()
        best, score = None, 0.0
        for n, other in enumerate(right):
            if n in used:
                continue
            s = difflib.SequenceMatcher(None, name, str(other.get("product") or other.get("item") or "").lower()).ratio()
            if s > score:
                best, score = n, s
        if best is not None and score >= 0.6:
            used.add(best)
            other = right[best]
            same = (other.get("price") == it.get("price") and str(other.get("units")) == str(it.get("units"))
                    and score > 0.9)
            rows.append({"": "✅" if same else "⚠️", "🧠 Normal": _line(it), "⚡ Low": _line(other)})
        else:
            rows.append({"": "❌", "🧠 Normal": _line(it), "⚡ Low": "—"})
    for n, other in enumerate(right):
        if n not in used:
            rows.append({"": "➕", "🧠 Normal": "—", "⚡ Low": _line(other)})
    if rows:
        st.caption("✅ same · ⚠️ different name, amount or price · ❌ only normal found it · ➕ only low found it")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
