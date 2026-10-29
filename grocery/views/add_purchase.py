"""Add purchase: receipt photos → (overlap questions) → review cards → save."""

import datetime as dt
import uuid

import streamlit as st

from grocery import photos, receipts, records, sheets, timeutil
from grocery.gemini import GeminiError
from grocery.schema import KINDS, STORAGE, UNITS
from grocery.views.common import back_home, go

S = "ap"  # session-state namespace for this screen


def render():
    back_home()
    st.title("Add purchase")
    mode = st.segmented_control("How", ["Receipt", "Barcode", "Say it"], default="Receipt",
                                label_visibility="collapsed", key="ap_mode")
    if mode == "Barcode":
        st.info("Coming in **Phase 2**: barcode photos and product info.")
        return
    if mode == "Say it":
        st.info("Coming in **Phase 3**: add a purchase by voice.")
        return

    state = st.session_state.setdefault(S, _fresh())
    {"photos": _photos_step, "questions": _questions_step,
     "review": _review_step, "saved": _saved_step}[state["stage"]](state)


def _fresh():
    return {"stage": "photos", "draft_id": f"D-{uuid.uuid4().hex[:8]}", "readings": [],
            "merged": [], "questions": [], "review": None, "interpretation": None}


def _reset():
    st.session_state[S] = _fresh()
    for k in [k for k in st.session_state if k.startswith(("ap_q_", "it_", "photos_queue"))]:
        del st.session_state[k]


# ---------------------------------------------------------------- photos

def _photos_step(state):
    model = sheets.get_settings()["model_receipts"]

    if not state["readings"] and not st.session_state.get("photos_queue"):
        _offer_resume(state)

    st.caption("Add the top of the receipt first. Long receipt? Add overlapping photos.")
    photos.pick_photos("photos_queue")

    # Read each new photo straight away and save the reading as a draft,
    # so a page reload can't lose it.
    queue = st.session_state.get("photos_queue", [])
    for n, photo in enumerate(queue):
        if photo.get("read"):
            continue
        photo_no = len(state["readings"]) + 1
        with st.spinner(f"Reading photo {photo_no}…"):
            try:
                reading = receipts.transcribe(model, photo["data"])
            except GeminiError as ex:
                st.error(f"Couldn't read {photo['name']}: {ex}")
                if st.button("Remove this photo", key=f"ap_drop_{n}"):
                    queue.pop(n)
                    st.rerun()
                return
        photo["read"] = True
        photo["photo_no"] = photo_no
        state["readings"].append({"photo_no": photo_no, "name": photo["name"], "model": model,
                                  "reading": reading, "data": photo["data"]})
        records.save_draft(state["draft_id"], photo_no, photo["name"], model, reading)

    if not state["readings"]:
        return

    st.subheader(f"Photos read ({len(state['readings'])})")
    for r in list(state["readings"]):
        with st.container(border=True, horizontal=True, vertical_alignment="center"):
            if r.get("data"):
                st.image(r["data"], width=56)
            rd = r["reading"]
            items = sum(1 for l in rd.get("lines", []) if l.get("kind") == "item")
            st.markdown(f"**Photo {r['photo_no']}** · {rd.get('store') or 'store not visible'}  \n{items} item lines")
            if st.button("Remove", key=f"ap_rm_{r['photo_no']}", type="tertiary"):
                state["readings"].remove(r)
                records.discard_draft(state["draft_id"], r["photo_no"])
                st.rerun()

    if st.button("Done adding photos", type="primary", width="stretch"):
        readings = receipts.order_photos([r["reading"] for r in state["readings"]])
        state["merged"], state["questions"] = receipts.merge(readings)
        state["stage"] = "questions" if state["questions"] else "review"
        st.rerun()
    if st.button("Start over", type="tertiary"):
        records.discard_draft(state["draft_id"])
        _reset()
        st.rerun()


def _offer_resume(state):
    drafts = records.load_drafts()
    for draft_id, d in drafts.items():
        with st.container(border=True):
            st.markdown(f"**Unsaved receipt** from {d['created_at']} · {len(d['readings'])} photo(s) already read")
            with st.container(horizontal=True):
                if st.button("Continue", key=f"ap_resume_{draft_id}", type="primary"):
                    state["draft_id"] = draft_id
                    state["readings"] = [{"photo_no": n, "name": name, "model": model, "reading": rd}
                                         for n, name, model, rd in d["readings"]]
                    st.rerun()
                if st.button("Discard", key=f"ap_discard_{draft_id}"):
                    records.discard_draft(draft_id)
                    st.rerun()


# ---------------------------------------------------------------- overlap questions

def _questions_step(state):
    st.subheader("Quick check")
    st.write("These items sit where two photos overlap, so I can't tell repeats from overlap.")
    answers = {}
    for n, q in enumerate(state["questions"]):
        with st.container(border=True):
            st.markdown(f"**{q['text']}**")
            answers[q["text"]] = st.number_input("How many did you buy?", min_value=1, step=1,
                                                 value=q["count"], key=f"ap_q_{n}")
    if st.button("Continue", type="primary", width="stretch"):
        state["merged"] = receipts.apply_answers(state["merged"], state["questions"], answers)
        state["stage"] = "review"
        st.rerun()


# ---------------------------------------------------------------- review

def _review_step(state):
    if state["review"] is None:
        _build_review(state)
        if state["review"] is None:
            return
    review = state["review"]

    st.subheader("Review")
    review["store"] = st.text_input("Store", value=review["store"], key="ap_store",
                                    help="Known stores: " + ", ".join(s["name"] for s in records.stores()) or None)
    review["date"] = st.date_input("Date", value=review["date"], key="ap_date", format="MM/DD/YYYY")
    review["total_paid"] = st.number_input("Total paid on receipt ($)", value=review["total_paid"],
                                           min_value=0.0, step=0.01, format="%.2f", key="ap_total")

    dup = records.find_duplicate_purchase(review["store"], review["date"].isoformat(), review["total_paid"])
    if dup:
        st.warning(f"Looks like a receipt you already saved: {dup['store']}, {dup['purchased_at']}, "
                   f"${dup['total_paid']}. Save only if this is a different purchase.")

    cards = review["items"]
    _price_check(review)

    open_by_item = records.open_batches_by_item()
    known = {records.norm(i["name"]): i for i in records.items()}
    needs = [c for c in cards if _needs_attention(c, known, open_by_item)]
    rest = [c for c in cards if c not in needs]

    if needs:
        st.markdown(f"#### Needs your attention ({len(needs)})")
        for c in needs:
            _card(c, review, known, open_by_item, open_fields=True)
    if rest:
        st.markdown(f"#### All other items ({len(rest)})")
        for c in rest:
            _card(c, review, known, open_by_item, open_fields=False)

    if review["skipped"]:
        with st.expander(f"Skipped lines ({len(review['skipped'])})"):
            for s in list(review["skipped"]):
                with st.container(horizontal=True, vertical_alignment="center"):
                    st.markdown(f"{s['text']} · {_money(s['amount'])}  \n*{s['reason']}*")
                    if st.button("Add back", key=f"ap_add_{s['uid']}", type="tertiary"):
                        review["skipped"].remove(s)
                        review["items"].append(_blank_card(s["text"], s["amount"]))
                        st.rerun()

    st.write("")
    if st.button(f"Save {len(cards)} items", type="primary", width="stretch", disabled=not cards):
        _save(state)
    if st.button("Back to photos", type="tertiary"):
        state["stage"], state["review"] = "photos", None
        st.rerun()


def _build_review(state):
    model = sheets.get_settings()["model_receipts"]
    readings = [r["reading"] for r in state["readings"]]
    info = receipts.summarize(readings)
    with st.spinner("Working out the items…"):
        try:
            result = receipts.interpret(model, info["store"], info["source_type"],
                                        state["merged"], records.items())
        except GeminiError as ex:
            st.error(f"Couldn't work out the items: {ex}")
            if st.button("Try again", type="primary"):
                st.rerun()
            return
    state["interpretation"] = (model, result)

    merged = state["merged"]
    known = {records.norm(i["name"]): i for i in records.items()}
    cards = []
    for it in result.get("items", []):
        existing = known.get(records.norm(it.get("item")))
        cards.append({
            "uid": uuid.uuid4().hex[:8],
            "item": existing["name"] if existing else (it.get("item") or "").strip(),
            "kind": it.get("kind") if it.get("kind") in KINDS else "Food",
            "unit": it.get("unit") if it.get("unit") in UNITS else "each",
            "storage": it.get("storage") if it.get("storage") in STORAGE else "Fridge",
            "perishable": bool(it.get("perishable")),
            "emoji": it.get("emoji") or "",
            "product": it.get("product") or "",
            "brand": it.get("brand") or "",
            "units": _num(it.get("units"), 1.0),
            "price": _num(it.get("price"), None),
            "unclear": bool(it.get("unclear")) or it.get("price") is None,
            "note": it.get("note") or "",
            "receipt_text": " / ".join(str(merged[i].get("text", "")) for i in it.get("lines", [])
                                       if isinstance(i, int) and 0 <= i < len(merged)),
        })
    skipped = []
    for s in result.get("skipped", []):
        lines = [merged[i] for i in s.get("lines", []) if isinstance(i, int) and 0 <= i < len(merged)]
        if lines:
            skipped.append({"uid": uuid.uuid4().hex[:8], "text": " / ".join(str(l.get("text", "")) for l in lines),
                            "amount": sum(_num(l.get("amount"), 0.0) for l in lines),
                            "reason": s.get("reason") or "Not a product"})

    date = _parse_date(info.get("date")) or timeutil.now().date()
    state["review"] = {
        "store": info.get("store") or "", "date": date, "time": info.get("time") or "",
        "source": info.get("source_type") or "printed_receipt",
        "total_paid": _num(info.get("total_paid"), 0.0), "tax": _num(info.get("tax"), None),
        "items": cards, "skipped": skipped,
    }


def _needs_attention(c, known, open_by_item):
    existing = known.get(records.norm(c["item"]))
    return c["unclear"] or existing is None or (existing and existing["item_id"] in open_by_item)


def _card(c, review, known, open_by_item, open_fields):
    uid = c["uid"]
    existing = known.get(records.norm(st.session_state.get(f"it_{uid}_item", c["item"])))
    with st.container(border=True):
        badges = []
        if existing is None:
            badges.append(":blue-badge[New item]")
        if c["unclear"]:
            badges.append(":orange-badge[Check this]")
        title = f"{c['emoji']} **{c['item'] or 'Unnamed item'}**".strip()
        st.markdown(f"{title} · {_money(c['price'])}  \n{' '.join(badges)}")
        st.caption(f"{c['product'] or '—'} · {c['units']:g} {c['unit']} · receipt: “{c['receipt_text']}”")
        if c["unclear"] and c["note"]:
            st.warning(c["note"])

        box = st.container() if open_fields else st.expander("Edit")
        with box:
            c["item"] = st.text_input("Item", value=c["item"], key=f"it_{uid}_item")
            c["product"] = st.text_input("Product / brand", value=c["product"], key=f"it_{uid}_product")
            c["units"] = st.number_input(f"Quantity ({c['unit']})", value=float(c["units"]), min_value=0.0,
                                         step=1.0, key=f"it_{uid}_units")
            c["price"] = st.number_input("Price paid, after discounts, no tax ($)",
                                         value=c["price"], min_value=0.0, step=0.01, format="%.2f",
                                         key=f"it_{uid}_price", placeholder="Enter the price")
            existing = known.get(records.norm(c["item"]))
            if existing is None:
                c["kind"] = st.selectbox("Kind", KINDS, index=KINDS.index(c["kind"]), key=f"it_{uid}_kind")
                c["unit"] = st.selectbox("Count it in", UNITS, index=UNITS.index(c["unit"]), key=f"it_{uid}_unit")
                if c["kind"] != "One-time":
                    c["storage"] = st.selectbox("Kept in", STORAGE, index=STORAGE.index(c["storage"]),
                                                key=f"it_{uid}_storage")
                    c["perishable"] = st.toggle("Perishable", value=c["perishable"], key=f"it_{uid}_perish")
            else:
                c["kind"], c["unit"], c["storage"] = existing["kind"], existing["unit"], existing["storage"]
                c["perishable"] = existing["perishable"] == "yes"
                st.caption(f"Known item · {existing['kind']} · counted in {existing['unit']} · "
                           f"{existing['storage']}")

        if existing is not None and existing["item_id"] in open_by_item:
            last = max(open_by_item[existing["item_id"]], key=lambda b: b.get("purchased_on", ""))
            answer = st.radio(f"Is your last {existing['name']} (bought {last['purchased_on']}) finished?",
                              ["Yes, finished", "No, still have it"], index=None, key=f"it_{uid}_done",
                              horizontal=True)
            c["finished_item_id"] = existing["item_id"] if answer == "Yes, finished" else None
            if answer == "Yes, finished":
                c["finished_on"] = st.date_input("Finished on", value=timeutil.now().date(),
                                                 key=f"it_{uid}_done_on", format="MM/DD/YYYY")

        if st.button("Remove", key=f"it_{uid}_rm", type="tertiary"):
            review["items"].remove(c)
            st.rerun()


def _price_check(review):
    prices = [c["price"] for c in review["items"] if c["price"] is not None]
    if not prices or not review["total_paid"]:
        return
    expected = review["total_paid"] - (review["tax"] or 0)
    total = sum(prices)
    if abs(total - expected) > 0.05:
        st.info(f"Items add up to **{_money(total)}**; the receipt says **{_money(expected)}** before tax. "
                "A line may be missing or misread.")


def _save(state):
    review = state["review"]
    problems = []
    if not review["store"].strip():
        problems.append("Enter the store.")
    for c in review["items"]:
        if not c["item"].strip():
            problems.append("Every item needs a name.")
            break
    if any(c["price"] is None for c in review["items"]):
        problems.append("Some items have no price.")
    if problems:
        for p in problems:
            st.error(p)
        return

    finished = {c["finished_item_id"]: c["finished_on"].isoformat()
                for c in review["items"] if c.get("finished_item_id")}
    payload = dict(review, date=review["date"].isoformat(),
                   total_paid=review["total_paid"] or "")
    readings = [("receipt_read", r["model"], r["reading"]) for r in state["readings"]]
    if state["interpretation"]:
        readings.append(("receipt_items", *state["interpretation"]))
    with st.spinner("Saving to your Google Sheet…"):
        try:
            _, count = records.save_purchase(payload, finished, state["draft_id"], readings)
        except Exception as ex:
            st.error(f"Saving failed: {ex}. Nothing was lost; try again.")
            return
    state["stage"], state["saved_count"] = "saved", count
    st.rerun()


def _saved_step(state):
    st.success(f"Saved {state.get('saved_count', 0)} items to your Google Sheet.")
    if st.button("Add another receipt", type="primary", width="stretch"):
        _reset()
        st.rerun()
    if st.button("Home", width="stretch"):
        _reset()
        go("home")


# ---------------------------------------------------------------- helpers

def _blank_card(text, amount):
    return {"uid": uuid.uuid4().hex[:8], "item": "", "kind": "Food", "unit": "each", "storage": "Fridge",
            "perishable": True, "emoji": "", "product": text, "brand": "", "units": 1.0,
            "price": amount, "unclear": True, "note": "Added back from skipped lines.", "receipt_text": text}


def _num(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _money(value):
    return "price?" if value is None else f"${value:,.2f}"


def _parse_date(text):
    try:
        return dt.date.fromisoformat(str(text))
    except ValueError:
        return None
