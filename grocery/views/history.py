"""History & undo: delete purchased items (one, or a whole receipt) and undo recent changes."""

import datetime as dt

import streamlit as st

from grocery import events, records, timeutil
from grocery.views.common import back_home


def render():
    back_home()
    st.title("History & undo")

    st.subheader("Recent purchases")
    st.caption("🗑 removes one item; every delete can be undone under Recent changes.")
    purchases = records.recent_purchases(20)
    if not purchases:
        st.caption("Nothing yet.")
    emoji = {i["item_id"]: i.get("emoji", "") for i in records.items()}
    for p, batches in purchases:
        pid = p["purchase_id"]
        with st.container(border=True):
            total = f" · ${p['receipt_total']}" if p.get("receipt_total") else ""
            when = f"{p['purchased_on']} {p.get('purchased_time', '')}".strip()
            with st.container(horizontal=True, vertical_alignment="center"):
                st.markdown(f"**{p['store']}** · {when}{total}{_odd_date(p)}  \n"
                            f":gray[Added {p.get('created_at', '')} by {p.get('source', '')}]")
                _fix_date(p)
            for b in batches:
                with st.container(horizontal=True, vertical_alignment="center"):
                    price = f" · ${b['price_paid']}" if b.get("price_paid") else ""
                    st.markdown(f"{emoji.get(b.get('item_id'), '')} {b['item']} :gray[{_amount(b)}{price}]".strip())
                    if st.button("", key=f"hist_rm_{b['batch_id']}", icon=":material/delete:", type="tertiary",
                                 help=f"Delete {b['item']} only (undoable)"):
                        events.apply([{"type": "delete", "batch": b}], said=f"History: deleted {b['item']}",
                                     source="history")
                        st.toast(f"Deleted {b['item']} · Undo under Recent changes")
                        st.rerun()
            if len(batches) < 2:
                continue
            if st.session_state.get("hist_confirm") == pid:
                st.warning(f"Delete all {len(batches)} items from this receipt? You can undo it.")
                with st.container(horizontal=True):
                    if st.button("Yes, delete all", key=f"hist_yes_{pid}", type="primary"):
                        events.apply([{"type": "delete", "batch": b} for b in batches],
                                     said=f"History: deleted receipt {p['store']} {p['purchased_on']}", source="history")
                        st.session_state.pop("hist_confirm", None)
                        st.toast("Receipt deleted · Undo under Recent changes")
                        st.rerun()
                    if st.button("Keep it", key=f"hist_no_{pid}"):
                        st.session_state.pop("hist_confirm", None)
                        st.rerun()
            elif st.button("Delete whole receipt", key=f"hist_del_{pid}", type="tertiary",
                           icon=":material/delete_sweep:"):
                st.session_state["hist_confirm"] = pid
                st.rerun()

    st.subheader("Recent changes")
    render_changes()


def _bought_on(p):
    try:
        return dt.date.fromisoformat(str(p.get("purchased_on", ""))[:10])
    except ValueError:
        return None


def _odd_date(p):
    """A receipt date far from the day it was added was probably misread (e.g. 2023 instead of 2026)."""
    bought, added = _bought_on(p), _bought_on({"purchased_on": p.get("created_at", "")})
    return " :orange-badge[date looks wrong?]" if bought and added and abs((added - bought).days) > 60 else ""


def _fix_date(p):
    """✏️ Fix the receipt's date: every item on it moves to the right day."""
    pid = p["purchase_id"]
    with st.popover("", icon=":material/edit_calendar:", key=f"hist_datepop_{pid}", help="Fix this receipt's date"):
        current = _bought_on(p) or timeutil.now().date()
        day = st.date_input("Bought on", value=current, max_value=timeutil.now().date(), format="MM/DD/YYYY",
                            key=f"hist_date_{pid}_{current}")
        st.caption("Changes the date of every item on this receipt; days open and lifespans follow.")
        if day != current and st.button("Save date", key=f"hist_datesave_{pid}", type="primary"):
            records.set_purchase_date(pid, day)
            st.toast(f"Receipt date: {day:%b %d, %Y}")
            st.rerun()


def render_changes():
    """Finished / wasted / expiry / delete changes, each with Undo (shared with Voice update)."""
    changes = events.recent(20)
    if not changes:
        st.caption("Nothing yet.")
        return
    for c in changes:
        with st.container(border=True, horizontal=True, vertical_alignment="center"):
            text = "  \n".join(c["lines"])
            st.markdown(f"~~{text}~~" if c["undone"] else text)
            if c["undone"]:
                st.caption("Undone")
            elif c["change_id"] and st.button("Undo", key=f"undo_{c['change_id']}", type="tertiary"):
                events.undo(c["change_id"])
                st.toast("Undone")
                st.rerun()


def _amount(b):
    try:
        return f"{float(b.get('units')):,g} {b.get('unit', '')}"
    except (TypeError, ValueError):
        return f"{b.get('units', '')} {b.get('unit', '')}".strip()
