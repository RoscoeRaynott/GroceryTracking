"""Item panel: tap an item in Pantry to see everything about it, fix what's wrong, add what's missing.

Opens as a side drawer; tapping outside closes it. It stays open across saves until you close it.
"""

import json

import altair as alt
import pandas as pd
import streamlit as st

from grocery import events, productinfo, recorder, records, sheets, voice
from grocery.gemini import GeminiError
from grocery.schema import KINDS, STORAGE, UNITS
from grocery.views import product_ui
from grocery.views.amounts import amount_text, pack_input

OPEN = "pantry_open"
PLACE_ICONS = {"Freezer": "🧊", "Fridge": "❄️", "Open air": "🧺"}


def profile_bar(checks):
    """Like a website's profile completion: ▰▰▰▰▰▰▱▱▱▱ 60%, red under 40%, orange under 80%, else green."""
    if not checks:
        return ""
    pct = round(100 * sum(1 for _, ok in checks if ok) / len(checks))
    filled = round(pct / 10)
    color = "red" if pct < 40 else "orange" if pct < 80 else "green"
    return f":{color}[{'▰' * filled}{'▱' * (10 - filled)} **{pct}%**]"


def open_panel(batch_id):
    st.session_state[OPEN] = batch_id


def show_if_open():
    """Call once per run, after the page: re-opens the panel for the item tapped last."""
    bid = st.session_state.get(OPEN)
    if not bid:
        return
    batch = next((b for b in records.live_batches() if b["batch_id"] == bid), None)
    if not batch or batch.get("status") != "open":
        st.session_state.pop(OPEN, None)
        return
    st.dialog(batch["item"], width="medium", position="right", on_dismiss=_close)(_panel)(bid)


def _close():
    st.session_state.pop(OPEN, None)


def _refresh():
    """Redraw just the panel; if this run wasn't a panel-only rerun, redraw the page (the panel reopens)."""
    product_ui.refresh()


def _panel(bid):
    batch = next((b for b in records.live_batches() if b["batch_id"] == bid), None)
    if not batch:
        return
    item = next((i for i in records.items() if i["item_id"] == batch.get("item_id")), {})
    product = next((p for p in records.products() if p["product_id"] == batch.get("product_id")), None)
    # Header: picture, names, and how complete its profile is (green chips known, gray still missing)
    with st.container(horizontal=True, vertical_alignment="center"):
        product_ui.photo(product, item.get("emoji", ""), width=72, item=item)
        brand = f"  \n:gray[{product.get('brand')}]" if product and product.get("brand") else ""
        st.markdown(f"**{product['name'] if product else item.get('name', '')}**{brand}")
        if product:
            _edit_names(product)
    checks = records.completeness(product, item, batch)
    st.markdown(profile_bar(checks) + "  \n" + " ".join(
        f":green-badge[✓ {what}]" if ok else f":gray-badge[{what}?]" for what, ok in checks))

    if product:
        _redo_search(product, item)

    # At a glance
    days = _days_open(batch)
    facts = [f":blue-badge[🗓 {batch.get('purchased_on', '')} · {batch.get('store', '')}]",
             f":violet-badge[🛒 {amount_text(batch.get('units'), batch.get('unit'))}]",
             f":gray-badge[⏱ {days}d open]"]
    if batch.get("price_paid"):
        facts.insert(1, f":green-badge[💲 {float(batch['price_paid']):,.2f}]")
    st.markdown(" ".join(facts))

    # Kept in · expires · pack: one compact row; tap a chip to change it
    with st.container(horizontal=True, gap="small"):
        _kept(item)
        _expiry(batch)
        if product:
            _pack(product, item)
    if product:
        if item.get("kind", "Food") == "Food":  # only food has ingredients and Nutrition Facts to track
            _ingredients(product, item)
            _nutrition(product)
            if st.session_state.get(f"ii_cam_{product['product_id']}"):
                st.markdown("**📸 Label photos** :gray[ingredients and/or nutrition panel; saving only adds what it read]")
                product_ui.label_photo(product, f"ii_lab_{product['product_id']}", inline=True)
        _names(product)
    else:
        st.caption("Loose item: no package details to fill in.")


FIELD_NAMES = {"photo_url": "photo", "serving_size": "serving size", "servings_per_pack": "servings",
               "ingredients": "ingredients", "nutrients_json": "nutrition", "abv": "ABV", "barcode": "barcode",
               "info_url": "page link", "pack size": "pack size"}


def _redo_search(product, item):
    """Big-chain / store-brand products: search that store's own site again and fill what's still missing."""
    store = records.lookup_store(product)
    if not store:
        return
    pid = product["product_id"]
    key = f"ii_redo_{pid}"
    found = st.session_state.get(key)
    if found is None:
        if st.button(f"🔎 Redo search on {store}'s site", key=f"{key}_go", type="secondary", width="stretch",
                     help=f"Look this product up on {store}'s own website and fill in what's missing"):
            with st.spinner(f"Searching {store}'s site…"):
                try:
                    st.session_state[key] = productinfo.web_lookup(
                        sheets.get_settings()["model_lookup"], records.product_search_text(product),
                        product.get("pack_unit") or item.get("unit") or "each", store=store)
                except productinfo.NotFound as ex:
                    st.session_state[key] = {"missing": str(ex) or "Not found."}
                except Exception as ex:
                    st.session_state[key] = {"missing": f"Search failed: {ex}"}
            _refresh()
        return
    with st.container(border=True):
        if found.get("missing"):
            st.warning(f"Not found on {store}'s site. {found['missing']}")
            if st.button("OK", key=f"{key}_ok"):
                st.session_state.pop(key, None)
                _refresh()
            return
        with st.container(horizontal=True, vertical_alignment="center"):
            if found.get("photo_url"):
                st.image(found["photo_url"], width=64)
            st.markdown(f"**{found.get('name', '')}**  \n:gray[{store} · is this it?]")
        if found.get("url"):
            st.markdown(f"[Open the page it used]({found['url']})")
        cols = productinfo.product_columns(found)
        new = [FIELD_NAMES.get(k, k) for k in ("serving_size", "ingredients", "nutrients_json", "abv")
               if cols.get(k) and not product.get(k)]
        if found.get("pack_units") and not records._float(product.get("pack_size")):
            each = found["pack_units"] / (found.get("pack_count") or 1)
            new.append(f"pack size {found.get('pack_count') or 1:g} × {each:g} {found.get('pack_unit', '')}")
        if found.get("brand") and not product.get("brand"):
            new.insert(0, f"brand {found['brand']}")
        if found.get("barcode") and not product.get("barcode"):
            new.append("barcode")
        st.markdown("Would fill: " + (" ".join(f":green-badge[+ {n}]" for n in new) or ":gray[nothing new]"))
        use_photo = st.checkbox("Use this photo", value=True, key=f"{key}_photo") if found.get("photo_url") else False
        saved = records._float(product.get("pack_size"))
        use_pack = False
        if found.get("pack_units") and saved:
            n_saved = int(records._float(product.get("pack_count")) or 1)
            n_found = int(found.get("pack_count") or 1)
            if abs(found["pack_units"] - saved) > 1e-6 or n_found != n_saved:
                unit = found.get("pack_unit") or product.get("pack_unit", "")
                st.markdown(f"Pack: saved :red-badge[{n_saved} × {amount_text(saved / n_saved, unit)}] → found "
                            f":green-badge[{n_found} × {amount_text(found['pack_units'] / n_found, unit)}]")
                use_pack = st.checkbox("Use the found pack", value=True, key=f"{key}_pack")
        with st.container(horizontal=True):
            if st.button("Yes, fill it in", key=f"{key}_yes", type="primary", disabled=not (new or use_photo or use_pack)):
                filled = records.fill_from_lookup(pid, found, use_photo, use_pack)
                st.session_state.pop(key, None)
                st.toast("Filled: " + ", ".join(FIELD_NAMES.get(f, f) for f in filled) if filled else "Nothing new")
                _refresh()
            if st.button("Not this", key=f"{key}_no"):
                st.session_state.pop(key, None)
                _refresh()


def _days_open(batch):
    from grocery import analytics, timeutil
    bought = analytics._date(batch.get("purchased_on"))
    return (timeutil.now().date() - bought).days if bought else 0


def _section(title):
    st.markdown(f"**{title}**")


def _kept(item):
    if item.get("kind") == "One-time":
        return
    current = item.get("storage") if item.get("storage") in STORAGE else "Fridge"
    perishable = item.get("perishable") == "yes"
    label = f"{PLACE_ICONS.get(current, '')} {current} · {'perishable' if perishable else 'keeps'}"
    if item.get("kind") == "Household":
        label = "🧴 · " + label
    with st.popover(label, key=f"ii_keptpop_{item['item_id']}"):
        st.caption("Every time you buy it")
        kind = item.get("kind") if item.get("kind") in KINDS else "Food"
        new_kind = st.segmented_control("Kind", KINDS, default=kind, key=f"ii_kind_{item['item_id']}",
                                        format_func=lambda k: {"Food": "🍎 Food", "Household": "🧴 Household",
                                                               "One-time": "📦 One-time"}.get(k, k),
                                        help="Only food has ingredients and nutrition to fill in")
        if new_kind and new_kind != kind:
            records.set_item(item["item_id"], kind=new_kind, tracked="no" if new_kind == "One-time" else "yes")
            st.toast(f"{item['name']}: {new_kind}")
            _refresh()
        choice = st.segmented_control("Kept in", STORAGE, default=current, key=f"ii_kept_{item['item_id']}",
                                      format_func=lambda s: f"{PLACE_ICONS.get(s, '')} {s}",
                                      label_visibility="collapsed")
        goes_bad = st.toggle("Goes bad (perishable)", value=perishable, key=f"ii_per_{item['item_id']}")
        change = {}
        if choice and choice != current:
            change["storage"] = choice
        if goes_bad != perishable:
            change["perishable"] = "yes" if goes_bad else "no"
        if change:
            records.set_item(item["item_id"], **change)
            st.toast(f"{item['name']}: saved for every purchase")
            _refresh()


def _expiry(batch):
    import datetime as dt
    current = dt.date.fromisoformat(batch["expiry_date"]) if batch.get("expiry_date") else None
    label = f"⏳ exp {current:%b %d}" if current else "⏳ no expiry"
    with st.popover(label, key=f"ii_exppop_{batch['batch_id']}"):
        st.caption("This purchase only")
        day = st.date_input("Expiry date", value=current, key=f"ii_exp_{batch['batch_id']}",
                            format="MM/DD/YYYY", label_visibility="collapsed")
        if day and day != current and st.button("Save", key=f"ii_exps_{batch['batch_id']}", type="primary"):
            events.apply([{"type": "expiry", "batch": batch, "expiry_date": day.isoformat()}],
                         said="Pantry: expiry set", source="pantry")
            st.toast("Expiry saved · Undo in History")
            _refresh()


def _pack(product, item):
    pid, unit = product["product_id"], product.get("pack_unit") or item.get("unit") or "each"
    known = records._float(product.get("pack_size"))
    old_count = records._float(product.get("pack_count"))
    if known:
        n = int(old_count or 1)
        label = f"📦 {n} × {amount_text(known / n, unit)}" if n > 1 else f"📦 {amount_text(known, unit)} pack"
    else:
        label = "📦 pack size?"
    with st.popover(label, key=f"ii_packpop_{pid}"):
        item_unit = item.get("unit") or unit
        new_unit = st.selectbox("Measured in", UNITS, index=UNITS.index(unit) if unit in UNITS else 0,
                                key=f"ii_unit_{pid}", help=f"How {item.get('name', 'this item')} is measured, for every purchase")
        if new_unit != unit:
            moved = records.convert(known, unit, new_unit)
            st.caption(f"{item.get('name', 'This item')} will be measured in **{new_unit}** from now on. "
                       + ("Amounts are converted." if moved is not None or not known else
                          "Purchases are recounted as packs × the size below."))
            known = moved
        size, count = pack_input(known, old_count, new_unit, key=f"ii_pack_{pid}_{new_unit}")
        changed_unit = new_unit != item_unit or new_unit != unit
        if size and (changed_unit or abs(size - (known or 0)) > 1e-9 or count != int(old_count or 1)) \
                and st.button("Save pack size", key=f"ii_packs_{pid}", type="primary"):
            if changed_unit:
                records.change_item_unit(item["item_id"], new_unit, pid)
            changed = records.apply_pack_size(pid, size, new_unit, count=count, recount=True)
            st.toast(f"Pack = {amount_text(size, new_unit)}" + (f" · {len(changed)} purchase(s) updated" if changed else ""))
            _refresh()


def _tools(pid, kind):
    """Small 🎤 / 📸 buttons next to a section; returns which one is open."""
    key = f"ii_mode_{pid}_{kind}"
    with st.container(horizontal=True):
        if kind == "ing" and st.button("", key=f"{key}_mic", icon=":material/mic:", help="Say the ingredients"):
            st.session_state[key] = None if st.session_state.get(key) == "mic" else "mic"
        if st.button("", key=f"{key}_cam", icon=":material/photo_camera:", help="Photograph the label"):
            cam = f"ii_cam_{pid}"  # one label camera for both sections, so photos and readings add up
            st.session_state[cam] = not st.session_state.get(cam)
    return st.session_state.get(key)


def _ingredients(product, item):
    pid = product["product_id"]
    with st.container(border=True):
        with st.container(horizontal=True, vertical_alignment="center"):
            _section("🧾 Ingredients")
            mode = _tools(pid, "ing")
        text = product.get("ingredients", "")
        if text:
            parts = [x.strip().replace("[", "(").replace("]", ")") for x in text.split(",") if x.strip()]
            st.markdown(" ".join(f":gray-badge[{x[:40]}]" for x in parts[:40]))
        elif product.get("info_status") == "none":
            st.caption("No label to read.")
        else:
            st.markdown(":orange-badge[missing] :gray[tap 🎤 to read it out, or 📸 to photograph it]")
            single = (item.get("name") or product["name"]).strip()
            if st.button(f"🌿 Single ingredient: {single}", key=f"ii_single_{pid}", type="tertiary",
                         help="For rice, turmeric powder and the like: the ingredient is the product itself"):
                records.save_ingredients(pid, single, source="Single ingredient")
                st.toast(f"Ingredients: {single}")
                _refresh()
        if mode == "mic":
            _say_ingredients(product)


def _say_ingredients(product):
    pid = product["product_id"]
    heard_key = f"ii_heard_{pid}"
    heard = st.session_state.get(heard_key)
    if heard is None:
        audio = recorder.record(f"ii_rec_{pid}", "Tap and read the ingredients")
        if audio:
            with st.spinner("Listening…"):
                try:
                    st.session_state[heard_key] = voice.ingredients_from_audio(audio, product["name"])
                except GeminiError as ex:
                    st.error(f"Couldn't hear that: {ex}")
                    return
            _refresh()
        return
    st.markdown("Heard: " + (heard or ":gray[nothing clear]"))
    with st.container(horizontal=True):
        if heard and st.button("Save", key=f"ii_hs_{pid}", type="primary"):
            records.save_ingredients(pid, heard)
            st.session_state.pop(heard_key, None)
            st.session_state.pop(f"ii_mode_{pid}_ing", None)
            st.toast("Ingredients saved")
            _refresh()
        if st.button("Try again", key=f"ii_hr_{pid}"):
            st.session_state.pop(heard_key, None)
            _refresh()


def _nutrition(product):
    pid = product["product_id"]
    try:
        per = json.loads(product.get("nutrients_json") or "{}").get("per_serving", {})
    except ValueError:
        per = {}
    with st.container(border=True):
        with st.container(horizontal=True, vertical_alignment="center"):
            _section("📊 Nutrition · % daily value")
            _tools(pid, "nut")
        rows = [{"nutrient": k.replace("_", " "), "dv": v["dv"]} for k, v in per.items()
                if isinstance(v, dict) and v.get("dv") is not None]
        if rows:
            if product.get("serving_size"):
                st.caption(f"Per serving: {product['serving_size']}")
            df = pd.DataFrame(rows)
            # FDA rule of thumb: 5% or less is low, 20% or more is high.
            df["level"] = pd.cut(df["dv"], [-1, 5, 19.999, 10_000], labels=["low", "medium", "high"])
            base = alt.Chart(df).encode(
                y=alt.Y("nutrient:N", sort=list(df.sort_values("dv", ascending=False)["nutrient"]), title=None, axis=alt.Axis(labelOverlap=False, labelLimit=140)))
            bars = base.mark_bar(cornerRadiusEnd=4).encode(
                x=alt.X("dv:Q", title=None, axis=None),
                color=alt.Color("level:N", scale=alt.Scale(domain=["low", "medium", "high"],
                                                           range=["#2ECC71", "#F39C12", "#E74C3C"]), legend=None),
                tooltip=["nutrient", "dv"])
            labels = base.mark_text(align="left", dx=4, color="#9AA0A6").encode(
                x="dv:Q", text=alt.Text("dv:Q", format=".0f"))
            st.altair_chart((bars + labels).properties(height=30 * len(rows)), width="stretch")
            st.caption("🟢 low (5% or less) · 🟠 medium · 🔴 high (20% or more)")
        elif product.get("info_status") == "none" or product.get("nutrition_status") == "none":
            st.caption("No nutrition to track.")
        else:
            st.markdown(":orange-badge[missing] :gray[tap 📸 to photograph the nutrition panel]")
            if st.button("🚫 No nutrition", key=f"ii_nonut_{pid}", type="tertiary",
                         help="You've checked: there's no nutrition label worth tracking for this"):
                records.mark_no_nutrition(pid)
                st.toast("Marked: no nutrition")
                _refresh()



def _edit_names(product):
    """✏️ beside the name: fix the product name or brand (the only place they're shown and edited)."""
    pid = product["product_id"]
    # Keys carry the saved values, so the boxes always start from what's saved now (e.g. after a redo search).
    tag = f"{pid}_{abs(hash((product['name'], product.get('brand', ''))))}"
    with st.popover("", icon=":material/edit:", key=f"ii_namepop_{pid}", help="Fix name or brand"):
        name = st.text_input("Product name", value=product["name"], key=f"ii_name_{tag}")
        brand = st.text_input("Brand", value=product.get("brand", ""), key=f"ii_brand_{tag}")
        if (name.strip() != product["name"] or brand.strip() != product.get("brand", "")) and name.strip() \
                and st.button("Save", key=f"ii_names_{pid}", type="primary"):
            records.rename_product(pid, name, brand)
            st.toast("Saved")
            _refresh()


def _names(product):
    pid = product["product_id"]
    busy = st.session_state.get(f"ii_nick_{pid}_open") or st.session_state.get(f"ii_nick_{pid}_heard") is not None
    with st.expander("🏷 Nicknames", expanded=bool(busy)):
        product_ui.nicknames(product, f"ii_nick_{pid}")
        if product.get("barcode") or product.get("info_url"):
            link = f" · [page]({product['info_url']})" if product.get("info_url") else ""
            st.caption(f"Barcode {product.get('barcode') or '—'}{link}")
