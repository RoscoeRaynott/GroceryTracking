"""Products still missing their pack size or label info; look each one up when you have a moment."""

import streamlit as st

from grocery import photos, productinfo, records, sheets
from grocery.gemini import GeminiError
from grocery.views import product_ui
from grocery.views.common import back_home


TAGS = {"size": "📏", "update": "🔄", "ingredients": "🧾"}


def render():
    back_home()
    st.title("Needs info")
    st.caption("📏 pack size · 🔄 purchases to update · 🧾 ingredients & nutrition. "
               "Fill each once; it's remembered for every future purchase.")

    products = records.products_needing_info()
    if not products:
        st.success("Nothing missing.")
        return

    for p in products:
        tags = " ".join(TAGS[m] for m in p["missing"])
        with st.expander(f"{tags}  {p.get('emoji', '')} {p['name']}".strip()):
            _fill(p)


def _fill(p):
    pid = p["product_id"]
    with st.container(horizontal=True, vertical_alignment="center"):
        product_ui.photo(p, p.get("emoji", ""), width=56, item=p.get("item_row"))
        st.markdown(f"**{p['name']}**  \n" + " ".join(
            {"size": ":orange-badge[pack size?]", "update": ":blue-badge[purchases to update]",
             "ingredients": ":orange-badge[ingredients?]"}[m]
            for m in p["missing"]))
    if "update" in p["missing"]:
        _update_purchases(p)
        return
    found = st.session_state.get(f"ni_found_{pid}")
    if found:
        _confirm(p, found)
        return

    # One search fills both the pack size and the label info.
    if st.button("Look it up online", key=f"ni_go_{pid}", icon=":material/travel_explore:", type="primary",
                 width="stretch"):
        query = st.session_state.get(f"ni_q_{pid}") or records.product_search_text(p)
        _lookup(pid, lambda: productinfo.web_lookup(sheets.get_settings()["model_lookup"], query,
                                                    records.item_unit(p["item_id"])))

    tab_label, tab_barcode, tab_web = st.tabs(["Label photo", "Barcode", "Edit search"])
    with tab_label:
        st.caption("Photograph the ingredients list and Nutrition Facts panel (1–3 photos). This always wins.")
        photos.pick_photos(f"ni_label_{pid}", "Tap to photograph the label", "Ingredients + Nutrition Facts")
        queue = st.session_state.get(f"ni_label_{pid}", [])
        if queue:
            st.caption(f"{len(queue)} photo(s) ready")
            if st.button("Read the label", key=f"ni_read_{pid}", type="primary"):
                with st.spinner("Reading the label…"):
                    try:
                        info = productinfo.read_label(sheets.get_settings()["model_labels"],
                                                      [ph["data"] for ph in queue[-3:]])
                        if not p.get("photo_url"):
                            info["photo_url"] = productinfo.thumbnail(queue[-3:][0]["data"])
                        _found(pid, info)
                    except GeminiError as ex:
                        st.error(f"Couldn't read the label: {ex}")
    with tab_barcode:
        photos.pick_photos(f"ni_bc_{pid}", "Tap to photograph its barcode")
        queue = st.session_state.get(f"ni_bc_{pid}", [])
        if queue and not queue[-1].get("done"):
            queue[-1]["done"] = True
            code = productinfo.read_barcode(queue[-1]["data"])
            if not code:
                st.warning("No barcode found in that photo. Try again closer.")
            else:
                _lookup(pid, lambda: productinfo.off_by_barcode(code), barcode=code)
    with tab_web:
        query = st.text_area("Search for", value=records.product_search_text(p), key=f"ni_q_{pid}",
                             help="Everything known about this product. Add or fix details to make the search exact.")
        st.caption("Fix the words above, then tap “Look it up online”.")

    with st.container(horizontal=True):
        if "ingredients" in p["missing"] and st.button("No label to read", key=f"ni_none_{pid}", type="tertiary"):
            records.mark_no_info(pid)
            st.rerun()
        if "size" in p["missing"] and st.button("Size varies / saved right", key=f"ni_size_{pid}", type="tertiary"):
            records.mark_size_ok(pid)
            st.rerun()
    with st.expander("Nicknames"):
        product_ui.nicknames(p, f"ni_nick_{pid}")


def _update_purchases(p):
    """The pack size is known but some purchases were saved before it: show the change, one tap."""
    pid, unit = p["product_id"], p.get("pack_unit") or ""
    st.markdown(f":blue-badge[1 pack = {float(p['pack_size']):,g} {unit}]")
    for b, new in p["resize"]:
        st.markdown(f"{b['purchased_on']} · {b.get('store', '')}: "
                    f":red-badge[{b['units']} {b['unit']}] → :green-badge[{new:,g} {unit or b['unit']}]")
    with st.container(horizontal=True):
        if st.button("Update them", key=f"ni_upd_{pid}", type="primary", icon=":material/sync:"):
            changed = records.apply_pack_size(pid, float(p["pack_size"]), unit)
            st.toast(f"Updated {len(changed)} purchase(s) of {p['name']}")
            st.rerun()
        if st.button("They're right as they are", key=f"ni_keep_{pid}", type="tertiary"):
            records.keep_amounts(pid)
            st.rerun()


def _lookup(pid, fetch, barcode=""):
    with st.spinner("Looking it up…"):
        try:
            _found(pid, fetch(), barcode)
        except productinfo.NotFound as ex:
            reason = str(ex) if str(ex) and " " in str(ex) else ""
            st.warning(f"Couldn't find this exact product. {reason} Try editing the search text, or the label photo.")
        except GeminiError as ex:
            st.error(f"Search failed: {ex}")
        except Exception as ex:
            st.error(f"Lookup failed: {type(ex).__name__}: {ex}")


def _found(pid, info, barcode=""):
    st.session_state[f"ni_found_{pid}"] = dict(info, barcode=barcode or info.get("barcode", ""))
    st.rerun()


def _pack_field(p, info):
    """One pack's size, from the lookup when it found one; always editable before saving."""
    pid = p["product_id"]
    unit = info.get("pack_unit") or records.item_unit(p["item_id"])
    known = records._float(info.get("pack_units")) or records._float(p.get("pack_size"))
    if not known and "size" not in p["missing"]:
        return
    size = st.number_input(f"1 pack = how many {unit}?", value=known, min_value=0.0, step=1.0, format="%g",
                           key=f"ni_pack_{pid}", placeholder=f"Type the {unit} in one pack")
    if not info.get("pack_units") and info.get("pack_text"):
        st.caption(f"The page says “{info['pack_text']}”, but no {unit} amount came back. Type it above.")
    info["pack_units"], info["pack_unit"] = (size or None), unit
    if size:
        waiting = records._to_resize([b for b in sheets.read_tab("batches")
                                      if b.get("product_id") == pid], size, unit)
        if waiting:
            st.caption("Also updates: " + ", ".join(f"{b['units']} {b['unit']} → {new:,g} {unit}"
                                                    for b, new in waiting))


def _confirm(p, info):
    pid = p["product_id"]
    sure = info["source"] == "Label photo"
    st.markdown(f"**{'Read from your label' if sure else 'Possible match, is this right?'}**")
    if info.get("photo_url"):
        st.image(info["photo_url"], width=120)
    st.markdown(f"{info.get('brand', '')} **{info.get('name', '')}**  \nFrom: {info['source']}")
    if info.get("reason"):
        st.caption(info["reason"])
    if info.get("pack_text"):
        size = f" = {info['pack_units']:g} {info['pack_unit']}" if info.get("pack_units") else ""
        st.markdown(f":blue-badge[pack: {info['pack_text']}{size}]")
    if info.get("url"):
        st.markdown(f"[Open the page it used]({info['url']})")
    _pack_field(p, info)
    if info.get("no_label_info"):
        st.info("Found the product, but its page lists no ingredients or Nutrition Facts "
                "(common for raw meat, fish and produce).")
        with st.container(horizontal=True):
            if st.button("Yes, that's it", key=f"ni_yes_{pid}", type="primary"):
                records.save_product_match(pid, info)
                _apply_pack(pid, info)
                del st.session_state[f"ni_found_{pid}"]
                st.toast(f"Saved: {info.get('name') or p['name']}")
                st.rerun()
            if st.button("Not this", key=f"ni_no_{pid}"):
                del st.session_state[f"ni_found_{pid}"]
                st.rerun()
        return
    for line in productinfo.summary(info):
        st.caption(line)
    if info.get("ingredients"):
        st.write(f"Ingredients: {info['ingredients']}")
    else:
        st.warning("No ingredients list in this result.")
    for title, url in info.get("sources", [])[1:3]:
        st.markdown(f"- [{title or 'other source'}]({url})")

    with st.container(horizontal=True):
        if st.button("Yes, save it", key=f"ni_yes_{pid}", type="primary"):
            records.save_product_info(pid, info, info.get("barcode", ""))
            _apply_pack(pid, info)
            del st.session_state[f"ni_found_{pid}"]
            st.toast(f"Saved info for {p['name']}")
            st.rerun()
        if st.button("Not this", key=f"ni_no_{pid}"):
            del st.session_state[f"ni_found_{pid}"]
            st.rerun()


def _apply_pack(pid, info):
    """A pack size fixes this product's purchases that were counted in packs or saved as just "1"."""
    if info.get("pack_units"):
        fixed = records.apply_pack_size(pid, info["pack_units"], info.get("pack_unit") or "each")
        if fixed:
            st.toast(f"Pack size {info['pack_units']:g} {info.get('pack_unit')} applied to {len(fixed)} purchase(s)")
