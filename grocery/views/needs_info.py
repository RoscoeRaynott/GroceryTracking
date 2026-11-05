"""Products still missing their pack size or label info; look each one up when you have a moment."""

from concurrent.futures import ThreadPoolExecutor, as_completed

import streamlit as st
from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx

from grocery import camera, photos, productinfo, records, sheets
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
    waiting = sum(1 for p in products if st.session_state.get(f"ni_found_{p['product_id']}"))
    if "ni_all_done" in st.session_state and not waiting:
        del st.session_state["ni_all_done"]  # every result has been answered
    if "ni_all_done" in st.session_state:
        found, missed = st.session_state["ni_all_done"]
        st.markdown(f":green-badge[✅ {found} found, check each below] "
                    + (f":orange-badge[⚠️ {missed} not found]" if missed else ""))

    todo = [p for p in products if set(p["missing"]) & {"size", "ingredients"}
            and not st.session_state.get(f"ni_found_{p['product_id']}")]
    if len(todo) >= 2:
        _look_up_all(todo)
    pickable = {p["product_id"] for p in todo} if len(todo) >= 2 else set()
    for p in products:
        tags = " ".join(TAGS[m] for m in p["missing"])
        pid = p["product_id"]
        mark = "✅ " if st.session_state.get(f"ni_found_{pid}") else "⚠️ " if st.session_state.get(f"ni_miss_{pid}") else ""
        with st.container(horizontal=True, vertical_alignment="top", gap="small"):
            if pid in pickable:
                st.checkbox("Include in Look up all", key=f"ni_pick_{pid}", label_visibility="collapsed")
            with st.expander(f"{mark}{tags}  {p.get('emoji', '')} {p['name']}".strip(),
                             expanded=bool(st.session_state.get(f"ni_found_{pid}"))):
                _fill(p)


MAX_AT_ONCE = 10


def _look_up_all(todo):
    """Look up the ticked products (all, unless you untick some), up to 10 at a time, with a progress bar.
    Results wait in each product's card for your Yes, exactly like a single lookup."""
    for p in todo:
        st.session_state.setdefault(f"ni_pick_{p['product_id']}", True)
    with st.container(horizontal=True):
        if st.button("Tick all", key="ni_pick_all", type="tertiary", icon=":material/check_box:"):
            for p in todo:
                st.session_state[f"ni_pick_{p['product_id']}"] = True
        if st.button("Untick all", key="ni_pick_none", type="tertiary", icon=":material/check_box_outline_blank:"):
            for p in todo:
                st.session_state[f"ni_pick_{p['product_id']}"] = False
    total = len(todo)
    todo = [p for p in todo if st.session_state.get(f"ni_pick_{p['product_id']}")]
    label = f"Look up all ({len(todo)})" if len(todo) == total else f"Look up ticked ({len(todo)} of {total})"
    if not st.button(label, icon=":material/travel_explore:", type="primary", width="stretch", disabled=not todo,
                     help="Untick products whose names need fixing first. Searches each ticked product with its "
                          "own search text, up to 10 at once. Nothing is saved until you tap Yes on each one."):
        return
    model = sheets.get_settings()["model_lookup"]
    jobs = {p["product_id"]: (st.session_state.get(f"ni_q_{p['product_id']}") or records.product_search_text(p),
                              records.item_unit(p["item_id"])) for p in todo}
    bar = st.progress(0.0, text=f"🔎 0 of {len(jobs)} searched")
    ctx = get_script_run_ctx()
    found = missed = 0
    with ThreadPoolExecutor(max_workers=MAX_AT_ONCE, initializer=lambda: add_script_run_ctx(ctx=ctx)) as pool:
        running = {pool.submit(productinfo.web_lookup, model, query, unit): pid for pid, (query, unit) in jobs.items()}
        for n, job in enumerate(as_completed(running), start=1):
            pid = running[job]
            st.session_state.pop(f"ni_miss_{pid}", None)
            try:
                info = job.result()
                st.session_state[f"ni_found_{pid}"] = dict(info, barcode=info.get("barcode", ""))
                found += 1
            except productinfo.NotFound as ex:
                st.session_state[f"ni_miss_{pid}"] = f"Not found. {ex}"
                missed += 1
            except Exception as ex:
                st.session_state[f"ni_miss_{pid}"] = f"Search failed: {ex}"
                missed += 1
            bar.progress(n / len(jobs), text=f"🔎 {n} of {len(jobs)} searched · ✅ {found} found · ⚠️ {missed} not found")
    st.session_state["ni_all_done"] = (found, missed)
    st.rerun()


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
    if st.session_state.get(f"ni_miss_{pid}"):
        st.warning(st.session_state[f"ni_miss_{pid}"] + " Edit the search text, or try the label photo.")

    # One search fills both the pack size and the label info.
    if st.button("Look it up online", key=f"ni_go_{pid}", icon=":material/travel_explore:", type="primary",
                 width="stretch"):
        query = st.session_state.get(f"ni_q_{pid}") or records.product_search_text(p)
        _lookup(pid, lambda: productinfo.web_lookup(sheets.get_settings()["model_lookup"], query,
                                                    records.item_unit(p["item_id"])))

    tab_web, tab_scan = st.tabs(["Edit search", "Scan it"])
    with tab_scan:
        st.caption("Scan the barcode to look it up, or tap the round button to photograph the ingredients "
                   "and nutrition panel (1–3 photos, any language). The label always wins.")
        got = camera.scan(f"ni_scan_{pid}")
        code = None
        if got and got[0] == "barcode":
            code = got[1]
        elif got:
            st.session_state.setdefault(f"ni_label_{pid}", []).append(got[1])
        with st.expander("Or pick photos from your gallery (barcode or label)"):
            photos.pick_photos(f"ni_gal_{pid}", "Tap to add photos")
        for ph in st.session_state.get(f"ni_gal_{pid}", []):
            if not ph.get("done"):
                ph["done"] = True
                found_code = productinfo.read_barcode(ph["data"])
                if found_code and not code:
                    code = found_code
                else:
                    st.session_state.setdefault(f"ni_label_{pid}", []).append(ph)
        if code:
            _lookup(pid, lambda: productinfo.by_barcode(
                sheets.get_settings()["model_lookup"], code,
                st.session_state.get(f"ni_q_{pid}") or records.product_search_text(p),
                records.item_unit(p["item_id"])), barcode=code)
        label = _label_queue(pid)
        if label:
            if not p.get("photo_url"):
                label["photo_url"] = productinfo.thumbnail(st.session_state[f"ni_label_{pid}"][-3:][0]["data"])
            st.session_state.pop(f"ni_label_{pid}", None)
            _found(pid, label)
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
    product_ui.merge(p, f"ni_merge_{pid}")


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


def _label_queue(pid, key_suffix=""):
    """Snapped label photos waiting to be read; returns the label info once "Read the label" is tapped."""
    queue = st.session_state.get(f"ni_label_{pid}", [])
    if not queue:
        return None
    with st.container(horizontal=True, vertical_alignment="center"):
        for ph in queue[-3:]:
            st.image(ph["data"], width=56)
        st.markdown(f":blue-badge[{len(queue[-3:])} label photo{'s' if len(queue[-3:]) != 1 else ''}]")
        if st.button("Clear", key=f"ni_clear_{pid}{key_suffix}", type="tertiary"):
            st.session_state.pop(f"ni_label_{pid}", None)
            st.rerun()
    if st.button("Read the label", key=f"ni_read_{pid}{key_suffix}", type="primary", icon=":material/document_scanner:"):
        with st.spinner("Reading the label…"):
            try:
                return productinfo.read_label(sheets.get_settings()["model_labels"], [ph["data"] for ph in queue[-3:]])
            except GeminiError as ex:
                st.error(f"Couldn't read the label: {ex}")
    return None


def _label_from_pack(p, info):
    """On a web or barcode match: photograph the pack's label to add the ingredients and nutrition."""
    pid = p["product_id"]
    with st.expander("📸 Have the pack? Photograph its label", expanded=bool(info.get("no_label_info"))):
        st.caption("Ingredients and nutrition panel, 1–3 photos, any language. The label wins over the web.")
        snapped = camera.photo(f"ni_labcam_{pid}", "Open camera to snap the label")
        if snapped:
            st.session_state.setdefault(f"ni_label_{pid}", []).append(snapped)
        with st.expander("Or pick label photos from your gallery"):
            photos.pick_photos(f"ni_label_{pid}", "Tap to add label photos")
        label = _label_queue(pid, "_m")
        if label:
            st.session_state.pop(f"ni_label_{pid}", None)
            _found(pid, productinfo.with_label(info, label), info.get("barcode", ""))


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
    st.session_state.pop(f"ni_miss_{pid}", None)
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
    sure = "label photo" in info["source"].lower()
    st.markdown(f"**{'Read from your label' if sure else 'Possible match, is this right?'}**")
    if info.get("photo_url"):
        st.image(info["photo_url"], width=120)
    st.markdown(f"{info.get('brand', '')} **{info.get('name', '')}**  \nFrom: {info['source']}")
    if info.get("reason"):
        st.caption(info["reason"])
    if info.get("pack_text"):
        size = f" = {info['pack_units']:g} {info['pack_unit']}" if info.get("pack_units") else ""
        st.markdown(f":blue-badge[pack: {info['pack_text']}{size}]")
    if info.get("size_note"):
        st.markdown(f":orange-badge[{info['size_note']}]")
    if info.get("url"):
        st.markdown(f"[Open the page it used]({info['url']})")
    _pack_field(p, info)
    if "label photo" not in info["source"].lower():
        _label_from_pack(p, info)
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
