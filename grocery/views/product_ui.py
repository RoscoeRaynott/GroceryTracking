"""Small pieces shown wherever a product appears: its photo and its nicknames."""

import streamlit as st
from streamlit.errors import StreamlitInvalidLayoutContextError

from grocery import recorder, records, voice
from grocery.gemini import GeminiError


def refresh():
    """Redraw only the panel or fragment this runs in (so an open item panel stays as it is);
    on a normal page, redraw the page."""
    try:
        st.rerun(scope="fragment")
    except StreamlitInvalidLayoutContextError:
        st.rerun()


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
                    refresh()

    if not st.session_state.get(f"{key}_open"):
        if st.button("Add a nickname", key=f"{key}_open_btn", icon=":material/mic:", type="tertiary"):
            st.session_state[f"{key}_open"] = True
            refresh()
        return
    nonce = st.session_state.setdefault(f"{key}_nn", 0)
    audio = recorder.record(f"{key}_mic_{nonce}", "Tap and say a nickname")
    typed = st.text_input("Nickname", key=f"{key}_txt_{nonce}", label_visibility="collapsed",
                          placeholder="or type: Costco egg whites; egg whites")
    add = st.button("Add nickname", key=f"{key}_add_{nonce}", disabled=not typed.strip())
    heard = st.session_state.get(f"{key}_heard")
    if audio:
        try:
            st.session_state[f"{key}_heard"] = voice.nicknames_from_audio(audio)
        except GeminiError as ex:
            st.error(f"Couldn't hear that: {ex}")
        refresh()
    new = []
    if heard is not None:
        # Show what was heard before saving, so a mishearing never sneaks in.
        st.markdown("Heard: " + (" ".join(f":blue-badge[{n}]" for n in heard) or ":gray[nothing clear]"))
        with st.container(horizontal=True):
            if heard and st.button("Save these", key=f"{key}_ok", type="primary"):
                new = heard
            if st.button("Try again", key=f"{key}_again"):
                st.session_state.pop(f"{key}_heard", None)
                refresh()
    elif add:
        new = [t for t in typed.split(";")]
    if new:
        st.session_state.pop(f"{key}_heard", None)
        records.add_nicknames(pid, new)
        st.session_state[f"{key}_nn"] = nonce + 1
        st.session_state[f"{key}_open"] = False
        st.toast("Nickname saved: " + ", ".join(n.strip() for n in new))
        refresh()


LABELS = {"photo_url": "photo", "barcode": "barcode", "brand": "brand", "serving_size": "serving size",
          "servings_per_pack": "servings", "ingredients": "ingredients", "nutrients_json": "nutrition",
          "abv": "ABV", "info_source": "info source", "info_url": "link", "info_status": "info checked",
          "pack_size": "pack size", "pack_unit": "pack unit", "packaged": "packaged"}


def merge(product, key, label="Same as a product I have", on_done=None):
    """“Same as a product I have”: fold this duplicate into the right product, after a preview.
    on_done(plan) runs after merging (e.g. to keep the item panel open on the purchase that was kept)."""
    others = [p for p in records.products() if p["product_id"] != product["product_id"]]
    # Same product bought more than once the same day (the store rang it up twice): offer to combine them.
    mine_open = [b for b in records.live_batches() if b.get("product_id") == product["product_id"] and b.get("status") == "open"]
    days = {}
    for b in mine_open:
        days.setdefault((b.get("purchased_on"), records.norm(b.get("store", ""))), []).append(b)
    twins = sum(len(v) for v in days.values() if len(v) > 1)
    if not others and not twins:
        return
    # Likely duplicates first: bought the same day (the store scanned it twice), then the same group.
    mine = {(b.get("purchased_on"), b.get("store")) for b in records.live_batches() if b.get("product_id") == product["product_id"]}
    same_day = {b.get("product_id") for b in records.live_batches() if (b.get("purchased_on"), b.get("store")) in mine}
    others.sort(key=lambda p: (p["product_id"] not in same_day, p.get("item_id") != product.get("item_id"),
                               p.get("item", ""), p["name"]))
    with st.popover(label, icon=":material/merge:", key=f"{key}_pop"):
        if twins:
            st.markdown(f":blue-badge[{twins} purchases of this, same day and store]")
            if st.button(f"Combine them into one", key=f"{key}_twins", type="primary", icon=":material/merge:"):
                combined = records.combine_same_day(product["product_id"])
                st.toast("Combined into one purchase")
                if on_done:
                    on_done({"combined": combined})
                refresh()
            if not others:
                return
            st.divider()
        st.caption(f"Merge **{product['name']}** into the product it really is. "
                   "Its purchases, nicknames and any missing details move over.")
        target = st.selectbox("It's the same as", others, index=None, key=f"{key}_pick",
                              format_func=lambda p: f"{p['name']}  ·  {p.get('item', '')}",
                              placeholder="Pick the product")
        if not target:
            return
        plan = records.merge_preview(product["product_id"], target["product_id"])
        with st.container(horizontal=True, vertical_alignment="center"):
            photo(product, width=40)
            st.markdown("→")
            photo(target, width=40)
            st.markdown(f"**{target['name']}**")
        n = len(plan["moved"])
        st.markdown(" ".join(
            [f":blue-badge[{n} purchase{'s' if n != 1 else ''} move]"]
            + [f":green-badge[+ {LABELS.get(c, c)}]" for c in plan["brings"] if c not in ("pack_unit", "info_source")]
            + [f":violet-badge[+ “{nm}”]" for nm in plan["nicknames"]]
            + [f":orange-badge[group → {target.get('item', '')}]"] * (target.get("item_id") != product.get("item_id"))
            + [f":blue-badge[{plan['same_day']} same-day purchase{'s' if plan['same_day'] != 1 else ''} → one]"] * bool(plan["same_day"])))
        st.caption(f"“{product['name']}” is then removed; {target['name']} keeps its own details where both have one.")
        if st.button("Merge", key=f"{key}_go", type="primary", icon=":material/merge:"):
            done = records.merge_products(product["product_id"], target["product_id"])
            st.toast(f"Merged into {target['name']}" + (" · same-day purchases combined" if done.get("combined") else ""))
            if on_done:
                on_done(done)
            refresh()
