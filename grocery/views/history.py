"""History & undo: delete recent purchases (e.g. test entries) and undo recent changes."""

import streamlit as st

from grocery import events, records
from grocery.views.common import back_home


def render():
    back_home()
    st.title("History & undo")

    st.subheader("Recent purchases")
    st.caption("Delete erases a purchase from your Sheet completely: its items, any changes to them, "
               "and items or products nothing else uses. Use it for test entries.")
    purchases = records.recent_purchases(20)
    if not purchases:
        st.caption("Nothing yet.")
    for p, batches in purchases:
        pid = p["purchase_id"]
        with st.container(border=True):
            total = f" · ${p['receipt_total']}" if p.get("receipt_total") else ""
            when = f"{p['purchased_on']} {p.get('purchased_time', '')}".strip()
            st.markdown(f"**{p['store']}** · {when}{total}  \n"
                        + ", ".join(f"{b['item']} ({b['units']} {b['unit']})" for b in batches))
            st.caption(f"Added {p.get('created_at', '')} by {p.get('source', '')}")
            if st.session_state.get("hist_confirm") == pid:
                st.warning("Delete this purchase and everything recorded about it?")
                with st.container(horizontal=True):
                    if st.button("Yes, delete it", key=f"hist_yes_{pid}", type="primary"):
                        with st.spinner("Deleting…"):
                            records.delete_purchase(pid)
                        st.session_state.pop("hist_confirm", None)
                        st.toast("Purchase deleted")
                        st.rerun()
                    if st.button("Keep it", key=f"hist_no_{pid}"):
                        st.session_state.pop("hist_confirm", None)
                        st.rerun()
            elif st.button("Delete", key=f"hist_del_{pid}", type="tertiary", icon=":material/delete:"):
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
