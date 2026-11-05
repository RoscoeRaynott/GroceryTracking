"""Small pieces shown wherever a product appears: its photo and its nicknames."""

import streamlit as st

from grocery import camera, photos, productinfo, recorder, records, sheets, voice
from grocery.gemini import GeminiError


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
                    st.rerun()

    if not st.session_state.get(f"{key}_open"):
        if st.button("Add a nickname", key=f"{key}_open_btn", icon=":material/mic:", type="tertiary"):
            st.session_state[f"{key}_open"] = True
            st.rerun()
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
        st.rerun()
    new = []
    if heard is not None:
        # Show what was heard before saving, so a mishearing never sneaks in.
        st.markdown("Heard: " + (" ".join(f":blue-badge[{n}]" for n in heard) or ":gray[nothing clear]"))
        with st.container(horizontal=True):
            if heard and st.button("Save these", key=f"{key}_ok", type="primary"):
                new = heard
            if st.button("Try again", key=f"{key}_again"):
                st.session_state.pop(f"{key}_heard", None)
                st.rerun()
    elif add:
        new = [t for t in typed.split(";")]
    if new:
        st.session_state.pop(f"{key}_heard", None)
        records.add_nicknames(pid, new)
        st.session_state[f"{key}_nn"] = nonce + 1
        st.session_state[f"{key}_open"] = False
        st.toast("Nickname saved: " + ", ".join(n.strip() for n in new))
        st.rerun()


LABELS = {"photo_url": "photo", "barcode": "barcode", "brand": "brand", "serving_size": "serving size",
          "servings_per_pack": "servings", "ingredients": "ingredients", "nutrients_json": "nutrition",
          "abv": "ABV", "info_source": "info source", "info_url": "link", "info_status": "info checked",
          "pack_size": "pack size", "pack_unit": "pack unit", "packaged": "packaged"}


def merge(product, key):
    """“Same as a product I have”: fold this duplicate into the right product, after a preview."""
    others = [p for p in records.products() if p["product_id"] != product["product_id"]]
    if not others:
        return
    # Same item first: that's where duplicates usually are.
    others.sort(key=lambda p: (p.get("item_id") != product.get("item_id"), p.get("item", ""), p["name"]))
    with st.popover("Same as a product I have", icon=":material/merge:", key=f"{key}_pop"):
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
            + [f":orange-badge[item → {target.get('item', '')}]"] * (target.get("item_id") != product.get("item_id"))))
        st.caption(f"“{product['name']}” is then removed; {target['name']} keeps its own details where both have one.")
        if st.button("Merge", key=f"{key}_go", type="primary", icon=":material/merge:"):
            records.merge_products(product["product_id"], target["product_id"])
            st.toast(f"Merged into {target['name']}")
            st.rerun()


def label_photo(product, key):
    """“Add label photo” for a product with no ingredients or nutrition yet (e.g. matched online without them)."""
    if product.get("ingredients") or product.get("nutrients_json"):
        return
    pid = product["product_id"]
    with st.popover("Add label photo", icon=":material/document_scanner:", key=f"{key}_pop"):
        info = st.session_state.get(f"{key}_info")
        if info:
            for line in productinfo.summary(info):
                st.caption(line)
            st.write(f"Ingredients: {info['ingredients']}" if info.get("ingredients") else "No ingredients read.")
            with st.container(horizontal=True):
                if st.button("Save", key=f"{key}_save", type="primary"):
                    records.save_product_info(pid, info)
                    st.session_state.pop(f"{key}_info", None)
                    st.toast(f"Label saved for {product['name']}")
                    st.rerun()
                if st.button("Discard", key=f"{key}_drop"):
                    st.session_state.pop(f"{key}_info", None)
                    st.rerun()
            return
        st.caption("Ingredients and nutrition panel, 1–3 photos, any language.")
        snapped = camera.photo(f"{key}_cam", "Open camera to snap the label")
        if snapped:
            st.session_state.setdefault(f"{key}_q", []).append(snapped)
        with st.expander("Or pick from your gallery"):
            photos.pick_photos(f"{key}_q", "Tap to add label photos")
        queue = st.session_state.get(f"{key}_q", [])[-3:]
        if queue:
            st.markdown(f":blue-badge[{len(queue)} photo{'s' if len(queue) != 1 else ''} ready]")
            if st.button("Read the label", key=f"{key}_read", type="primary"):
                with st.spinner("Reading the label…"):
                    try:
                        st.session_state[f"{key}_info"] = productinfo.read_label(
                            sheets.get_settings()["model_labels"], [ph["data"] for ph in queue])
                        st.session_state.pop(f"{key}_q", None)
                    except Exception as ex:
                        st.error(f"Couldn't read the label: {ex}")
                st.rerun()
