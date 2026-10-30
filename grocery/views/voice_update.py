"""Voice update: say what finished, went bad or expires → preview → approve. Undo in Recent changes."""

import streamlit as st

from grocery import events, recorder, voice
from grocery.gemini import GeminiError
from grocery.views import add_purchase, history
from grocery.views.common import back_home, go

UPDATE_TYPES = ("waste", "finish", "expiry", "delete")


def render():
    back_home()
    st.title("Voice update")

    pending = st.session_state.get("vu")
    if pending:
        _preview(pending)
    else:
        _listen()

    _recent_changes()


def _listen():
    st.caption("e.g. “Two avocados and half the spinach went bad. Butter finished three days ago. "
               "The milk expires November 2nd.”")
    nonce = st.session_state.setdefault("vu_nonce", 0)
    audio = recorder.record(f"vu_mic_{nonce}")
    typed = st.text_input("Or type it", key=f"vu_text_{nonce}")
    go_typed = st.button("Use typed text", key=f"vu_go_{nonce}", disabled=not typed.strip())
    if not (audio or go_typed):
        return
    with st.spinner("Listening…"):
        try:
            result, batch_refs, history_refs = voice.understand(
                voice.model(), audio=audio,
                text=None if audio else typed, mode="update")
        except GeminiError as ex:
            st.error(f"Couldn't understand that: {ex}")
            return
    st.session_state["vu_nonce"] = nonce + 1
    st.session_state["vu"] = {"result": result, "batch_refs": batch_refs, "history_refs": history_refs}
    st.rerun()


def _preview(pending):
    result, refs = pending["result"], pending["batch_refs"]
    st.markdown(f"> {result.get('transcript', '')}")

    actions, problems = [], []
    for a in result.get("actions", []):
        if a.get("type") in UPDATE_TYPES:
            batch = refs.get(a.get("batch") or "")
            if batch:
                actions.append(dict(a, batch=batch))
            else:
                problems.append(f"Couldn't find an open batch for: “{a.get('said', '')}”")

    for n, q in enumerate(result.get("questions", [])):
        options = [o for o in q.get("options", []) if o.get("label")]
        if not options:
            continue
        pick = st.radio(q.get("text", "Which one?"), [o["label"] for o in options], index=None, key=f"vu_q_{n}")
        chosen = next((o for o in options if o["label"] == pick), None)
        action = q.get("action") or {}
        if chosen and chosen.get("batch") in refs and action.get("type") in UPDATE_TYPES:
            actions.append(dict(action, batch=refs[chosen["batch"]]))

    adds = [a for a in result.get("actions", []) if a.get("type") == "add"]

    st.subheader("This will happen:")
    if not actions and not adds:
        st.info("Nothing to do yet." + (" Answer the question above." if result.get("questions") else ""))
    with st.container(border=True):
        for a in actions:
            st.markdown(f"- {_line(a)}")
        for a in adds:
            st.markdown(f"- Add purchase: {a.get('units', '')} {a.get('unit', '')} {a.get('item', '')} "
                        f"({a.get('store') or 'store?'}) · opens review before saving")
    for p in problems:
        st.warning(p)
    if result.get("not_understood"):
        st.caption(f"Didn't understand: {result['not_understood']}")

    with st.container(horizontal=True):
        if st.button("Approve", type="primary", disabled=not (actions or adds)):
            if actions:
                events.apply([dict(a, type=a["type"]) for a in actions],
                             said=result.get("transcript", ""), source="voice")
            st.session_state.pop("vu", None)
            if adds:
                state = st.session_state.setdefault("vc", add_purchase._fresh("vc"))
                add_purchase.start_voice_review(state, result, pending["history_refs"])
                st.session_state["ap_mode"] = "Say it"
                go("add")
            st.toast(f"Saved {len(actions)} change(s)")
            st.rerun()
        if st.button("Cancel"):
            st.session_state.pop("vu", None)
            st.rerun()


def _line(a):
    b = a["batch"]
    name = f"**{b['item']}** (bought {b.get('purchased_on', '?')}, {b.get('store', '')})"
    if a["type"] == "waste":
        wasted = events.fraction_text(float(a.get("fraction") or 0))
        if a.get("batch_done", True):
            return f"{name}: {wasted} wasted, the rest eaten · batch closed on {a.get('happened_on')}"
        return f"{name}: {wasted} wasted on {a.get('happened_on')} · rest still left, stays open"
    if a["type"] == "finish":
        rest = " · the rest was thrown away" if a.get("rest_wasted") else ""
        return f"{name}: finished on {a.get('happened_on')}{rest} · batch closed"
    if a["type"] == "expiry":
        return f"{name}: expires {a.get('expiry_date')}"
    return f"{name}: delete this batch"


def _recent_changes():
    st.subheader("Recent changes")
    history.render_changes()
