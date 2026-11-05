"""Item panel: tap an item in Pantry to see everything about it, fix what's wrong, add what's missing.

Opens as a side drawer; tapping outside closes it. It stays open across saves until you close it.
"""

import json

import altair as alt
import pandas as pd
import streamlit as st

from grocery import events, recorder, records, voice
from grocery.gemini import GeminiError
from grocery.schema import STORAGE
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
    checks = records.completeness(product, item, batch)
    st.markdown(profile_bar(checks) + "  \n" + " ".join(
        f":green-badge[✓ {what}]" if ok else f":gray-badge[{what}?]" for what, ok in checks))

    # At a glance
    days = _days_open(batch)
    facts = [f":blue-badge[🗓 {batch.get('purchased_on', '')} · {batch.get('store', '')}]",
             f":violet-badge[📦 {amount_text(batch.get('units'), batch.get('unit'))}]",
             f":gray-badge[⏱ {days}d open]"]
    if batch.get("price_paid"):
        facts.insert(1, f":green-badge[💲 {float(batch['price_paid']):,.2f}]")
    if batch.get("expiry_date"):
        facts.append(f":orange-badge[⏳ exp {batch['expiry_date']}]")
    st.markdown(" ".join(facts))

    _kept(item)
    _expiry(batch)
    if product:
        _pack(product, item)
        _ingredients(product, item)
        _nutrition(product)
        _names(product)
    else:
        st.caption("Loose item: no package details to fill in.")


def _days_open(batch):
    from grocery import analytics, timeutil
    bought = analytics._date(batch.get("purchased_on"))
    return (timeutil.now().date() - bought).days if bought else 0


def _section(title):
    st.markdown(f"**{title}**")


def _kept(item):
    if item.get("kind") == "One-time":
        return
    with st.container(border=True):
        _section("📍 Kept in · every time you buy it")
        current = item.get("storage") if item.get("storage") in STORAGE else "Fridge"
        choice = st.segmented_control("Kept in", STORAGE, default=current, key=f"ii_kept_{item['item_id']}",
                                      format_func=lambda s: f"{PLACE_ICONS.get(s, '')} {s}",
                                      label_visibility="collapsed")
        perishable = st.toggle("Goes bad (perishable)", value=item.get("perishable") == "yes",
                               key=f"ii_per_{item['item_id']}")
        change = {}
        if choice and choice != current:
            change["storage"] = choice
        if perishable != (item.get("perishable") == "yes"):
            change["perishable"] = "yes" if perishable else "no"
        if change:
            records.set_item(item["item_id"], **change)
            st.toast(f"{item['name']}: saved for every purchase")
            _refresh()


def _expiry(batch):
    import datetime as dt
    with st.container(border=True):
        _section("⏳ Expires (this purchase)")
        current = dt.date.fromisoformat(batch["expiry_date"]) if batch.get("expiry_date") else None
        with st.container(horizontal=True, vertical_alignment="bottom"):
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
    with st.container(border=True):
        _section("📦 One pack" + ("" if known else "  :orange-badge[size?]"))
        old_count = records._float(product.get("pack_count"))
        size, count = pack_input(known, old_count, unit, key=f"ii_pack_{pid}")
        if size and (abs(size - (known or 0)) > 1e-9 or count != int(old_count or 1)) \
                and st.button("Save pack size", key=f"ii_packs_{pid}", type="primary"):
            changed = records.apply_pack_size(pid, size, unit, count=count)
            st.toast(f"Pack = {amount_text(size, unit)}" + (f" · {len(changed)} purchase(s) updated" if changed else ""))
            _refresh()


def _tools(pid, kind):
    """Small 🎤 / 📸 buttons next to a section; returns which one is open."""
    key = f"ii_mode_{pid}_{kind}"
    with st.container(horizontal=True):
        if kind == "ing" and st.button("", key=f"{key}_mic", icon=":material/mic:", help="Say the ingredients"):
            st.session_state[key] = None if st.session_state.get(key) == "mic" else "mic"
        if st.button("", key=f"{key}_cam", icon=":material/photo_camera:", help="Photograph the label"):
            st.session_state[key] = None if st.session_state.get(key) == "cam" else "cam"
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
        elif mode == "cam":
            product_ui.label_photo(product, f"ii_lab_{pid}", inline=True)


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
            mode = _tools(pid, "nut")
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
        if mode == "cam":
            product_ui.label_photo(product, f"ii_labn_{pid}", inline=True)


def _names(product):
    pid = product["product_id"]
    busy = st.session_state.get(f"ii_nick_{pid}_open") or st.session_state.get(f"ii_nick_{pid}_heard") is not None
    with st.expander("✏️ Name, brand, nicknames", expanded=bool(busy)):
        name = st.text_input("Product name", value=product["name"], key=f"ii_name_{pid}")
        brand = st.text_input("Brand", value=product.get("brand", ""), key=f"ii_brand_{pid}")
        if (name.strip() != product["name"] or brand.strip() != product.get("brand", "")) and name.strip() \
                and st.button("Save names", key=f"ii_names_{pid}", type="primary"):
            records.rename_product(pid, name, brand)
            st.toast("Saved")
            _refresh()
        product_ui.nicknames(product, f"ii_nick_{pid}")
        if product.get("barcode") or product.get("info_url"):
            link = f" · [page]({product['info_url']})" if product.get("info_url") else ""
            st.caption(f"Barcode {product.get('barcode') or '—'}{link}")
