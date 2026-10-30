"""Food products still missing ingredients / nutrition, and ways to fill them in."""

import streamlit as st

from grocery import photos, productinfo, records, sheets
from grocery.gemini import GeminiError
from grocery.views import product_ui
from grocery.views.common import back_home


def render():
    back_home()
    st.title("Needs info")
    st.caption("Packaged foods with no ingredients yet. Fill each one once; it's remembered for every future purchase.")

    products = records.products_needing_info()
    if not products:
        st.success("Every packaged food has its info. Nothing to do.")
        return

    for p in products:
        with st.expander(f"{p.get('emoji', '')} {p['name']}".strip()):
            _fill(p)


def _fill(p):
    pid = p["product_id"]
    with st.container(horizontal=True, vertical_alignment="center"):
        product_ui.photo(p, p.get("emoji", ""), width=56)
        st.markdown(f"**{p['name']}**")
    product_ui.nicknames(p, f"ni_nick_{pid}")
    found = st.session_state.get(f"ni_found_{pid}")
    if found:
        _confirm(p, found)
        return

    tab_label, tab_barcode, tab_web = st.tabs(["Label photo", "Barcode", "Search the web"])
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
        st.caption("You'll check the result before it's saved.")
        if st.button("Search", key=f"ni_web_{pid}", type="primary", disabled=not query.strip()):
            def search():
                try:
                    return productinfo.off_by_name(p.get("brand", ""), p["name"])
                except productinfo.NotFound:
                    return productinfo.web_lookup(sheets.get_settings()["model_lookup"], query)
            _lookup(pid, search)

    st.divider()
    if st.button("This product has no label to read", key=f"ni_none_{pid}", type="tertiary"):
        records.mark_no_info(pid)
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


def _confirm(p, info):
    pid = p["product_id"]
    sure = info["source"] == "Label photo"
    st.markdown(f"**{'Read from your label' if sure else 'Possible match, is this right?'}**")
    if info.get("photo_url"):
        st.image(info["photo_url"], width=120)
    st.markdown(f"{info.get('brand', '')} **{info.get('name', '')}**  \nFrom: {info['source']}")
    if info.get("reason"):
        st.caption(info["reason"])
    if info.get("url"):
        st.markdown(f"[Open the page it used]({info['url']})")
    if info.get("no_label_info"):
        st.info("Found the product, but its page lists no ingredients or Nutrition Facts "
                "(common for raw meat, fish and produce).")
        with st.container(horizontal=True):
            if st.button("Yes, that's it", key=f"ni_yes_{pid}", type="primary"):
                records.save_product_match(pid, info)
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
            del st.session_state[f"ni_found_{pid}"]
            st.toast(f"Saved info for {p['name']}")
            st.rerun()
        if st.button("Not this", key=f"ni_no_{pid}"):
            del st.session_state[f"ni_found_{pid}"]
            st.rerun()
