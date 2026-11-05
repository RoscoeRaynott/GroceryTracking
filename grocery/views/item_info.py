"""Item panel: tap an item in Pantry to see everything about it, fix what's wrong, add what's missing.

Opens as a side drawer; tapping outside closes it. It stays open across saves until you close it.
"""

import json
import zlib

import altair as alt
import pandas as pd
import streamlit as st

from grocery import analytics, camera, events, productinfo, recorder, records, sheets, voice
from grocery.gemini import GeminiError
from grocery.schema import KINDS, STORAGE, UNITS
from grocery.views import product_ui
from grocery.views.amounts import amount_input, amount_text, pack_input

OPEN = "pantry_open"
PLACE_ICONS = {"Freezer": "🧊", "Fridge": "❄️", "Open air": "🧺"}


def profile_bar(checks, compact=False):
    """Like a website's profile completion: ▰▰▰▰▰▰▱▱▱▱ 60%, red under 40%, orange under 80%, else green.
    compact: five segments and no number, for the Pantry tiles."""
    if not checks:
        return ""
    pct = round(100 * sum(1 for _, ok in checks if ok) / len(checks))
    color = "red" if pct < 40 else "orange" if pct < 80 else "green"
    if compact:
        filled = round(pct / 20)
        return f":{color}[{'▰' * filled}{'▱' * (5 - filled)}]"
    filled = round(pct / 10)
    return f":{color}[{'▰' * filled}{'▱' * (10 - filled)} **{pct}%**]"


def _k(base, *saved):
    """A widget key tied to the saved values it shows: when they change anywhere (a lookup, voice, another
    screen), the box starts again from what's saved instead of showing an old value."""
    return f"{base}_{zlib.crc32(repr(saved).encode()):x}"


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
    product = next((p for p in records.products() if p["product_id"] == batch.get("product_id")), None)
    item = next((i for i in records.items() if i["item_id"] == batch.get("item_id")), {"name": batch["item"]})
    st.dialog(records.short_name(product, item), width="medium", position="right", on_dismiss=_close)(_panel)(bid)


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
        _picture(product, item)
        brand = f"  \n:gray[{product.get('brand')}]" if product and product.get("brand") else ""
        st.markdown(f"**{product['name'] if product else item.get('name', '')}**{brand}")
        if product:
            _edit_names(product)
    _quick_actions(batch)
    checks = records.completeness(product, item, batch)
    st.markdown(profile_bar(checks) + "  \n" + " ".join(
        f":green-badge[✓ {what}]" if ok else f":gray-badge[{what}?]" for what, ok in checks))

    if product:
        _find_info(product, item)

    # At a glance
    days = _days_open(batch)
    facts = [f":blue-badge[🗓 {batch.get('purchased_on', '')} · {batch.get('store', '')}]",
             f":gray-badge[⏱ {days}d open]"]
    if batch.get("price_paid"):
        facts.insert(1, f":green-badge[💲 {float(batch['price_paid']):,.2f}]")
    st.markdown(" ".join(facts))

    # Kept in · expires · pack: one compact row; tap a chip to change it
    with st.container(horizontal=True, gap="small"):
        _kept(item)
        if item.get("perishable") == "yes":  # only things that go bad need an expiry date (same rule as the bar)
            _expiry(batch, item)
        _bought(batch, product)
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


def _picture(product, item):
    """The picture; tap the 🔍 to see it big."""
    product_ui.photo(product, item.get("emoji", ""), width=72, item=item)
    url = (product or {}).get("photo_url") or (item.get("photo_url") if item.get("photo_url") not in ("", "none") else "")
    if url:
        with st.popover("", icon=":material/zoom_in:", key=f"ii_zoom_{item.get('item_id')}_{(product or {}).get('product_id', '')}"):
            st.image(url, width="stretch")


def _find_info(product, item):
    """Find what's missing: search (the store's own site for chain / store-brand products, else anywhere),
    or scan the barcode or label. A match is previewed and fills only what's empty."""
    store = records.lookup_store(product)
    pid = product["product_id"]
    key = f"ii_redo_{pid}"
    found = st.session_state.get(key)
    if found is None:
        unit = product.get("pack_unit") or item.get("unit") or "each"
        label = f"🔎 Redo search on {store}'s site" if store else "🔎 Look it up online"
        with st.container(horizontal=True, gap="small"):
            go_search = st.button(label, key=f"{key}_go", type="secondary",
                                  help="Search the web and fill in what's missing")
            with st.popover("", icon=":material/edit_note:", key=f"{key}_words", help="Search words"):
                st.text_area("Search for", value=records.product_search_text(product), key=_k(f"ii_q_{pid}", records.product_search_text(product)),
                             help="Fix or add words to make the search exact")
            with st.popover("📷 Scan", key=f"{key}_scan", help="Scan the barcode, or photograph the label"):
                got = camera.scan(f"ii_scan_{pid}", "Scan barcode or label")
                st.caption("Point at the barcode, or photograph the front (name) or the label. It's saved for "
                           "🔎 Look up all, so you can go straight to the next item.")
        if go_search:
            with st.spinner(f"Searching {store + chr(39) + 's site' if store else 'the web'}…"):
                st.session_state[key] = _lookup(lambda: productinfo.web_lookup(
                    sheets.get_settings()["model_lookup"], st.session_state.get(_k(f"ii_q_{pid}", records.product_search_text(product)))
                    or records.product_search_text(product), unit, store=store))
            _refresh()
        # Scanning only saves what was scanned; the lookups run later, all together (Pantry: 🔎 Look up all).
        if got:
            queue = st.session_state.setdefault(SCANS, {}).setdefault(pid, {"photos": []})
            if got[0] == "barcode":
                records.set_barcode(pid, got[1])
                queue["barcode"] = got[1]
                st.toast("Barcode saved · look it up later with 🔎 Look up all")
            else:
                queue["photos"].append(got[1]["data"])
                st.toast(f"Photo saved ({len(queue['photos'])}) · look it up later with 🔎 Look up all")
            _refresh()
        queued = st.session_state.get(SCANS, {}).get(pid)
        if queued:
            bits = ([f"barcode {queued['barcode']}"] if queued.get("barcode") else []) + \
                   ([f"{len(queued['photos'])} photo{'s' if len(queued['photos']) != 1 else ''}"] if queued.get("photos") else [])
            st.markdown(f":blue-badge[📷 waiting: {' · '.join(bits)}] :gray[looked up with 🔎 Look up all]")
            with st.container(horizontal=True):
                if st.button("Look up now", key=f"{key}_now", type="primary"):
                    with st.spinner("Looking it up…"):
                        st.session_state[key] = _lookup(lambda: scan_lookup(product, item))
                    st.session_state[SCANS].pop(pid, None)
                    _refresh()
                if st.button("Clear", key=f"{key}_clear", type="tertiary"):
                    st.session_state[SCANS].pop(pid, None)
                    _refresh()
        return
    with st.container(border=True):
        if found.get("missing"):
            st.warning(f"Not found{f' on {store}' + chr(39) + 's site' if store else ''}. {found['missing']}")
            if st.button("OK", key=f"{key}_ok"):
                st.session_state.pop(key, None)
                _refresh()
            return
        with st.container(horizontal=True, vertical_alignment="center"):
            if found.get("photo_url"):
                st.image(found["photo_url"], width=64)
            st.markdown(f"**{found.get('name', '')}**  \n:gray[{found.get('source', '')} · is this it?]")
        if found.get("url"):
            st.markdown(f"[Open the page it used]({found['url']})")
        cols = productinfo.product_columns(found)
        photo = found.get("from_photo") or ([k for k in ("serving_size", "ingredients", "nutrients_json", "abv") if cols.get(k)]
                                            if found.get("source") == "Label photo" else [])
        new = [FIELD_NAMES.get(k, k) + (" (from your photo)" if k in photo else "")
               for k in ("serving_size", "ingredients", "nutrients_json", "abv")
               if cols.get(k) and (not product.get(k) or k in photo)]
        replace = []  # saved ingredients / nutrition the found page has different values for
        for col, what in (("ingredients", "ingredients"), ("nutrients_json", "nutrition")):
            if cols.get(col) and product.get(col) and col not in photo and _differs(col, cols[col], product[col]):
                st.markdown(f"{what.capitalize()}: :red-badge[{_short(col, product[col])}] → :green-badge[{_short(col, cols[col])}]")
                if st.checkbox(f"Use the found {what}", value=True, key=f"{key}_use_{col}"):
                    replace.append(col)
        new_name = new_brand = None
        f_name, f_brand = str(found.get("name") or "").strip(), str(found.get("brand") or "").strip()
        if f_name and records.norm(f_name) != records.norm(product["name"]):
            st.markdown(f"Name: :red-badge[{product['name']}] → :green-badge[{f_name}]")
            if st.checkbox("Use the found name", value=True, key=f"{key}_name"):
                new_name = f_name
        if f_brand and product.get("brand") and records.norm(f_brand) != records.norm(product["brand"]):
            st.markdown(f"Brand: :red-badge[{product['brand']}] → :green-badge[{f_brand}]")
            if st.checkbox("Use the found brand", value=True, key=f"{key}_brand"):
                new_brand = f_brand
        new_item = None
        exact = records.short_name({"name": f_name or product["name"], "brand": f_brand or product.get("brand", "")}, item)
        if records.needs_own_item({"name": f_name or product["name"], "brand": f_brand or product.get("brand", "")}, item):
            st.markdown(f"Item: :red-badge[{item.get('name', '')}] → :green-badge[{exact}] "
                        ":gray[its own item, so its shelf life is learned on its own]")
            # Ticked when the item lumps several products together (two masalas under "Spice mix").
            shared = sum(1 for p in records.products() if p.get("item_id") == item.get("item_id")) > 1
            if st.checkbox("Make it its own item", value=shared, key=f"{key}_item"):
                new_item = st.text_input("Item name", value=exact, key=f"{key}_item_name").strip() or None
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
            if st.button("Yes, use it", key=f"{key}_yes", type="primary",
                         disabled=not (new or use_photo or use_pack or new_name or new_brand or replace or new_item)):
                filled = records.fill_from_lookup(pid, found, use_photo, use_pack, new_name, new_brand, replace, new_item)
                st.session_state.pop(key, None)
                st.toast("Filled: " + ", ".join(FIELD_NAMES.get(f, f) for f in filled) if filled else "Nothing new")
                _refresh()
            if st.button("Not this", key=f"{key}_no"):
                st.session_state.pop(key, None)
                _refresh()


SCANS = "scan_queue"  # {product_id: {"barcode": digits, "photos": [jpeg bytes]}}: scanned, not yet looked up


def scan_lookup(product, item):
    """One product's best lookup: its waiting photos, else its barcode (scanned or saved), else its name."""
    settings = sheets.get_settings()
    queued = st.session_state.get(SCANS, {}).get(product["product_id"], {})
    return productinfo.best_lookup(
        settings["model_lookup"], settings["model_labels"],
        st.session_state.get(_k(f"ii_q_{product['product_id']}", records.product_search_text(product)))
        or records.product_search_text(product),
        product.get("pack_unit") or item.get("unit") or "each", records.lookup_store(product),
        queued.get("barcode") or product.get("barcode", ""), queued.get("photos", ()))


def _differs(col, a, b):
    if col == "nutrients_json":
        try:
            return json.loads(a) != json.loads(b)
        except ValueError:
            return a != b
    return records.norm(a) != records.norm(b)


def _short(col, value):
    """A one-glance summary for a saved → found line."""
    if col == "nutrients_json":
        try:
            n = json.loads(value).get("per_serving", {})
        except ValueError:
            return "?"
        return f"{len(n)} nutrients" + (f", {n['calories']['amount']:g} kcal" if "calories" in n else "")
    text = str(value).replace("[", "(").replace("]", ")")
    return text if len(text) <= 40 else text[:38] + "…"


def _lookup(fetch):
    try:
        return fetch()
    except productinfo.NotFound as ex:
        return {"missing": str(ex) if " " in str(ex) else "Not found."}
    except Exception as ex:
        return {"missing": f"Search failed: {ex}"}


def _bought(batch, product):
    """🛒 What you bought this time: always a number of packs. Packs of a fixed size give the total; for things
    sold by weight (size varies) this purchase also records what it weighed."""
    bid, unit = batch["batch_id"], batch.get("unit") or "each"
    units = records._float(batch.get("units")) or 0.0
    product = product or {}
    varies = product.get("pack_varies") == "yes"
    size = records._float(product.get("pack_size")) if product.get("pack_unit") in ("", None, unit) else None
    packs = records._float(batch.get("packs"))
    shown_packs = packs if packs and packs >= 1 and abs(packs - round(packs)) < 1e-9 else None
    if varies or size:
        n_text = f"{(shown_packs or 1):g} pack{'s' if (shown_packs or 1) != 1 else ''}"
        label = f"🛒 {n_text} · {amount_text(units, unit)}"
    else:
        label = f"🛒 {amount_text(units, unit)}"
    with st.popover(label, key=f"ii_boughtpop_{bid}"):
        st.caption("What you bought this time")
        n = st.number_input("Packs bought", value=float(shown_packs or (round(units / size, 2) if size and units else 1.0)),
                            min_value=0.0, step=1.0, format="%g", key=_k(f"ii_packs_bought_{bid}", units, packs, size, varies))
        if varies:  # sold by weight: this purchase's own weight
            total = amount_input("This time it weighed", units, unit, key=_k(f"ii_amount_bought_{bid}", units))
        elif size:
            total = n * size
            st.caption(f"= {n:g} × {amount_text(size, unit)} = **{amount_text(total, unit)}**")
        else:
            st.caption("No pack size yet: set it in 📦, or mark it as sold by weight there.")
            total = amount_input("Amount bought", units, unit, key=_k(f"ii_amount_bought_{bid}", units))
        if total is not None and (abs(total - units) > 1e-9 or n != (packs or 0)) \
                and st.button("Save", key=f"ii_bought_save_{bid}", type="primary"):
            records.set_bought(bid, total, n)
            st.toast(f"Bought: {n:g} pack{'s' if n != 1 else ''} · {amount_text(total, unit)}")
            _refresh()


def _quick_actions(batch):
    """Finished / Some went bad (they used to sit on each Pantry card), plus why it's flagged, if it is."""
    bid = batch["batch_id"]
    row = next((r for r in analytics.pantry() if r["batch"]["batch_id"] == bid), None)
    if row and row["reason"]:
        st.markdown(f":{'red' if row['level'] == 'red' else 'orange'}-badge[⚠️ {row['reason']}]")
    with st.container(horizontal=True):
        if st.button("Finished", key=f"pf_{bid}", icon=":material/check:", type="primary"):
            events.apply([{"type": "finish", "batch": batch}], said="Pantry: Finished", source="pantry")
            st.toast(f"{batch['item']} finished · Undo in History")
            _close()
            st.rerun()  # the item leaves the Pantry, so close the panel and redraw the page
        with st.popover("Some went bad", icon=":material/delete:", key=f"pop_{bid}"):
            _went_bad(batch)


def _went_bad(b):
    bid = b["batch_id"]
    parsed = st.session_state.get(f"pw_{bid}")
    if parsed is None:
        text = st.text_input("What went bad?", key=f"pwt_{bid}",
                             placeholder="e.g. half · 200 grams · one bell pepper · 2 slices, rest is fine")
        if st.button("Check", key=f"pwc_{bid}", type="primary", disabled=not text.strip()):
            with st.spinner("Reading…"):
                try:
                    st.session_state[f"pw_{bid}"] = dict(voice.parse_waste(b, text), text=text)
                except GeminiError as ex:
                    st.error(f"Couldn't read that: {ex}")
                    return
            _refresh()
        return

    share = events.fraction_text(float(parsed.get("fraction") or 0))
    done = parsed.get("batch_done", True)
    st.markdown(f":orange-badge[{share} wasted] "
                + (":gray-badge[rest eaten · closes]" if done else ":green-badge[rest still left]"))
    st.caption(parsed.get("summary", ""))
    with st.container(horizontal=True):
        if st.button("Approve", key=f"pwa_{bid}", type="primary"):
            events.apply([{"type": "waste", "batch": b, "fraction": parsed.get("fraction"),
                           "batch_done": done, "happened_on": parsed.get("happened_on")}],
                         said=f"Pantry: {parsed['text']}", source="pantry")
            st.session_state.pop(f"pw_{bid}", None)
            st.toast(f"Saved · Undo in History")
            _refresh()
        if st.button("Cancel", key=f"pwx_{bid}"):
            st.session_state.pop(f"pw_{bid}", None)
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
        new_kind = st.segmented_control("Kind", KINDS, default=kind, key=_k(f"ii_kind_{item['item_id']}", kind),
                                        format_func=lambda k: {"Food": "🍎 Food", "Household": "🧴 Household",
                                                               "One-time": "📦 One-time"}.get(k, k),
                                        help="Only food has ingredients and nutrition to fill in")
        if new_kind and new_kind != kind:
            records.set_item(item["item_id"], kind=new_kind, tracked="no" if new_kind == "One-time" else "yes")
            st.toast(f"{item['name']}: {new_kind}")
            _refresh()
        choice = st.segmented_control("Kept in", STORAGE, default=current, key=_k(f"ii_kept_{item['item_id']}", current),
                                      format_func=lambda s: f"{PLACE_ICONS.get(s, '')} {s}",
                                      label_visibility="collapsed")
        goes_bad = st.toggle("Goes bad (perishable)", value=perishable, key=_k(f"ii_per_{item['item_id']}", perishable))
        change = {}
        if choice and choice != current:
            change["storage"] = choice
        if goes_bad != perishable:
            change["perishable"] = "yes" if goes_bad else "no"
        if change:
            records.set_item(item["item_id"], **change)
            st.toast(f"{item['name']}: saved for every purchase")
            _refresh()


def _expiry(batch, item):
    import datetime as dt
    current = dt.date.fromisoformat(batch["expiry_date"]) if batch.get("expiry_date") else None
    undated = item.get("no_expiry") == "yes"
    label = f"⏳ exp {current:%b %d}" if current else "⏳ no date printed" if undated else "⏳ expiry?"
    with st.popover(label, key=f"ii_exppop_{batch['batch_id']}"):
        if st.toggle("No date printed on it", value=undated, key=_k(f"ii_noexp_{item['item_id']}", undated),
                     help="For every purchase of this item (e.g. avocados). How long it really lasts is learned "
                          "from your own history in Insights.") != undated:
            records.set_no_expiry(item["item_id"], not undated)
            st.toast("No date printed: counted as done" if not undated else "Expiry date wanted again")
            _refresh()
        if undated and not current:
            return
        st.caption("This purchase only")
        day = st.date_input("Expiry date", value=current, key=_k(f"ii_exp_{batch['batch_id']}", current),
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
    varies = product.get("pack_varies") == "yes"
    if varies:
        label = f"📦 size varies ({unit})"
    elif known:
        n = int(old_count or 1)
        label = f"📦 pack: {n} × {amount_text(known / n, unit)}" if n > 1 else f"📦 pack: {amount_text(known, unit)}"
    else:
        label = "📦 pack size?"
    with st.popover(label, key=f"ii_packpop_{pid}"):
        now_varies = st.toggle("Size varies (sold by weight)", value=varies, key=_k(f"ii_varies_{pid}", varies),
                               help="Salmon, a pack of chicken, produce by the pound: each pack weighs something "
                                    "different, so each purchase records its own weight in 🛒")
        if now_varies != varies:
            records.set_pack_varies(pid, now_varies)
            st.toast("Size varies: each purchase keeps its own weight" if now_varies else "Fixed pack size")
            _refresh()
        item_unit = item.get("unit") or unit
        new_unit = st.selectbox("Measured in", UNITS, index=UNITS.index(unit) if unit in UNITS else 0,
                                key=_k(f"ii_unit_{pid}", unit), help=f"How {item.get('name', 'this item')} is measured, for every purchase")
        if new_unit != unit:
            moved = records.convert(known, unit, new_unit)
            st.caption(f"{item.get('name', 'This item')} will be measured in **{new_unit}** from now on. "
                       + ("Amounts are converted." if moved is not None or not known else
                          "Purchases are recounted as packs × the size below."))
            known = moved
        if varies:
            st.caption("Each purchase records its own weight in 🛒.")
            if new_unit != unit and st.button("Save", key=f"ii_packs_{pid}", type="primary"):
                records.change_item_unit(item["item_id"], new_unit, None)  # weights simply convert (lb <-> oz)
                _refresh()
            return
        size, count = pack_input(known, old_count, new_unit, key=_k(f"ii_pack_{pid}_{new_unit}", known, old_count))
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
            if str(product.get("info_source", "")).startswith("USDA"):
                piece = records._float(product.get("piece_grams"))
                st.caption(f"📚 Typical values from USDA ({product['info_source'].split(': ', 1)[-1]}), per 100 g"
                           + (f" · 1 piece ≈ {piece:g} g" if piece else ""))
        elif product.get("nutrition_status") == records.NO_NUTRITION:
            with st.container(horizontal=True, vertical_alignment="center"):
                st.caption("You chose: no nutrition.")
                if st.button("Undo", key=f"ii_nonut_undo_{pid}", type="tertiary"):
                    records.sheets.update_rows("products", "product_id", {pid: {"nutrition_status": ""}})
                    _refresh()
        else:
            st.markdown(":orange-badge[missing] :gray[📸 photograph the panel, use typical values, or say there's none]")
        # Two clear choices: typical USDA values, or no nutrition. Either one counts as done.
        if not rows and product.get("nutrition_status") != records.NO_NUTRITION:
            if st.session_state.get(f"ii_usda_{pid}") is not None:
                _typical(product)  # the match picker is open: give it the full width
                return
            with st.container(horizontal=True):
                _typical(product)
                if st.button("🚫 No nutrition", key=f"ii_nonut_{pid}", type="tertiary",
                             help="You've seen the typical values option and don't want nutrition for this"):
                    records.mark_no_nutrition(pid)
                    st.toast("Marked: no nutrition")
                    _refresh()
        elif str(product.get("info_source", "")).startswith("USDA"):
            _typical(product)  # change the typical match


def _typical(product):
    from grocery import usda
    pid = product["product_id"]
    if not usda.api_key():
        return
    item = next((i for i in records.items() if i["item_id"] == product.get("item_id")), {})
    key = f"ii_usda_{pid}"
    options = st.session_state.get(key)
    if options is None:
        label = "📚 Change typical match" if str(product.get("info_source", "")).startswith("USDA") else "📚 Typical nutrition (USDA)"
        if st.button(label, key=f"{key}_go", type="tertiary",
                     help="Typical values for fruit, vegetables, meat and fish, from the USDA food database"):
            with st.spinner("Asking USDA…"):
                try:
                    st.session_state[key] = usda.matches(item.get("name") or product["name"])
                except Exception as ex:
                    st.session_state[key] = []
                    st.error(f"USDA: {ex}")
            _refresh()
        return
    with st.container(border=True):
        if not options:
            st.warning("No USDA match found.")
            if st.button("OK", key=f"{key}_ok"):
                st.session_state.pop(key, None)
                _refresh()
            return
        pick = st.selectbox("USDA match", options, format_func=lambda m: m["name"], key=f"{key}_pick")
        n = pick["nutrients"]
        st.markdown("Per 100 g: " + " ".join(
            f":gray-badge[{k.replace('_', ' ')} {v['amount']:g}{productinfo.NUTRIENT_UNITS.get(k, '') if k != 'calories' else ' kcal'}]"
            for k, v in n.items() if k in ("calories", "potassium", "sodium", "protein", "dietary_fiber", "total_sugars")))
        grams, portion = None, ""
        if item.get("unit") == "each":  # counted in pieces: how much does one weigh?
            gkey = f"{key}_g_{pick['fdc_id']}"
            if gkey not in st.session_state:
                try:
                    st.session_state[gkey] = usda.piece_grams(pick["fdc_id"])
                except Exception:
                    st.session_state[gkey] = (None, "")
            grams, portion = st.session_state[gkey]
            grams = st.number_input("1 piece weighs (g)", value=grams, min_value=0.0, step=5.0, format="%g",
                                    key=f"{key}_grams_{pick['fdc_id']}", help=f"USDA: {portion}" if portion else None)
        with st.container(horizontal=True):
            if st.button("Use it", key=f"{key}_yes", type="primary"):
                if records.save_typical(pid, item.get("name") or product["name"], pick, grams, portion):
                    st.toast(f"📚 Typical nutrition: {pick['name']}")
                else:
                    st.warning("This product already has nutrition from its label, so it was kept.")
                st.session_state.pop(key, None)
                _refresh()
            if st.button("Cancel", key=f"{key}_no"):
                st.session_state.pop(key, None)
                _refresh()



def _edit_names(product):
    """✏️ beside the name: fix the product name or brand (the only place they're shown and edited)."""
    pid = product["product_id"]
    # Keys carry the saved values, so the boxes always start from what's saved now (e.g. after a redo search).
    tag = _k(pid, product["name"], product.get("brand", ""))
    with st.popover("", icon=":material/edit:", key=f"ii_namepop_{pid}", help="Fix name or brand"):
        name = st.text_input("Product name", value=product["name"], key=f"ii_name_{tag}")
        no_brand = product.get("no_brand") == "yes"
        if st.toggle("No brand (e.g. loose avocados)", value=no_brand, key=_k(f"ii_nobrand_{pid}", no_brand)) != no_brand:
            records.set_no_brand(pid, not no_brand)
            st.toast("No brand" if not no_brand else "Brand can be filled in")
            _refresh()
        brand = st.text_input("Brand", value=product.get("brand", ""), key=f"ii_brand_{tag}", disabled=no_brand)
        if (name.strip() != product["name"] or brand.strip() != product.get("brand", "")) and name.strip() \
                and st.button("Save", key=f"ii_names_{pid}", type="primary"):
            records.rename_product(pid, name, brand)
            st.toast("Saved")
            _refresh()
        item_name = product.get("item", "")
        new_item = st.text_input("Item (for shelf life)", value=item_name, key=_k(f"ii_itemname_{pid}", item_name),
                                 help="Products with the same item share one 'how long it lasts'. Give a product its own "
                                      "item when it lasts differently (a one-use masala vs a jar of garam masala).")
        if new_item.strip() and records.norm(new_item) != records.norm(item_name) \
                and st.button("Move to this item", key=f"ii_moveitem_{pid}", type="primary"):
            records.move_to_item(pid, new_item)
            st.toast(f"Now its own item: {new_item.strip()}")
            _refresh()


def _names(product):
    pid = product["product_id"]
    busy = st.session_state.get(f"ii_nick_{pid}_open") or st.session_state.get(f"ii_nick_{pid}_heard") is not None
    with st.expander("🏷 Nicknames", expanded=bool(busy)):
        product_ui.nicknames(product, f"ii_nick_{pid}")
        if product.get("barcode") or product.get("info_url"):
            link = f" · [page]({product['info_url']})" if product.get("info_url") else ""
            st.caption(f"Barcode {product.get('barcode') or '—'}{link}")
