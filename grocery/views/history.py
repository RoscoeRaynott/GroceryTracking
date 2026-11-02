"""History & undo: delete purchased items (one, or a whole receipt) and undo recent changes."""

import streamlit as st

from grocery import events, records
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
            st.markdown(f"**{p['store']}** · {when}{total}  \n:gray[Added {p.get('created_at', '')} by {p.get('source', '')}]")
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
