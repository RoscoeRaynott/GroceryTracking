"""Add purchase.

Receipt: photos → (overlap questions) → review cards → save or discard.
Barcode: barcode photos → product lookup → review cards (enter prices) → save or discard.
Say it: speak or type what you bought → review cards → save or discard.
"""

import datetime as dt
import uuid

import streamlit as st

from grocery import photos, productinfo, receipts, recorder, records, sheets, timeutil, voice
from grocery.gemini import GeminiError
from grocery.schema import KINDS, STORAGE, UNITS
from grocery.views.common import back_home, go

STATES = {"Receipt": "ap", "Barcode": "bc", "Say it": "vc"}  # session-state namespace per mode


def render():
    back_home()
    st.title("Add purchase")
    st.session_state.setdefault("ap_mode", "Receipt")  # Voice update may preset "Say it"
    mode = st.segmented_control("How", ["Receipt", "Barcode", "Say it"],
                                label_visibility="collapsed", key="ap_mode")
    mode = mode or "Receipt"

    key = STATES[mode]
    state = st.session_state.setdefault(key, _fresh(key))
    {"photos": _photos_step, "questions": _questions_step, "scan": _scan_step, "speak": _speak_step,
     "review": _review_step, "saved": _saved_step}[state["stage"]](state)


def _fresh(key):
    return {"k": key, "stage": {"ap": "photos", "bc": "scan", "vc": "speak"}[key],
            "draft_id": f"D-{uuid.uuid4().hex[:8]}" if key == "ap" else "", "readings": [],
            "merged": [], "questions": [], "scans": [], "review": None, "interpretation": None}


def _reset(state):
    key = state["k"]
    st.session_state[key] = _fresh(key)
    queue = {"ap": "photos_queue", "bc": "barcode_queue", "vc": "vc_"}[key]
    for k in [k for k in st.session_state if k.startswith((f"{key}_", f"it_", queue))]:
        if k != "ap_mode":
            del st.session_state[k]


# ---------------------------------------------------------------- photos

def _photos_step(state):
    model = sheets.get_settings()["model_receipts"]

    if not state["readings"] and not st.session_state.get("photos_queue"):
        _offer_resume(state)

    st.caption("Add the top of the receipt first. Long receipt? Add overlapping photos.")
    photos.pick_photos("photos_queue", "Tap to add receipt photos or screenshots",
                       "Pick one or several. Long receipt? Add overlapping photos.")

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
        _reset(state)
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


# ---------------------------------------------------------------- barcode scans

def _scan_step(state):
    state["store"] = st.text_input("Store", value=state.get("store") or records.last_store(), key="bc_store_in")
    st.caption(f"Date: today ({timeutil.now():%b %d}). You'll enter prices on the next screen.")
    photos.pick_photos("barcode_queue", "Tap to photograph a barcode",
                       "One item per photo. Fill most of the photo with the barcode.")

    queue = st.session_state.get("barcode_queue", [])
    for photo in queue:
        if photo.get("done"):
            continue
        photo["done"] = True
        code = productinfo.read_barcode(photo["data"])
        if not code:
            st.warning("No barcode found in that photo. Try again closer and steadier, or type the digits below.")
            continue
        _add_scan(state, code)

    with st.expander("Type barcode digits instead"):
        digits = st.text_input("Barcode digits", key="bc_digits")
        if st.button("Look up", key="bc_lookup") and digits.strip().isdigit():
            _add_scan(state, digits.strip())

    if not state["scans"]:
        return
    st.subheader(f"Scanned ({len(state['scans'])})")
    for sc in list(state["scans"]):
        with st.container(border=True):
            if sc["name"]:
                st.markdown(f"**{sc['name']}**  \n{sc['brand'] or ''} {sc.get('quantity') or ''}")
            else:
                sc["name"] = st.text_input("Not found online. What is it?", key=f"bc_name_{sc['uid']}",
                                           placeholder="e.g. Kirkland Italian Chicken Sausage")
            st.caption(f"{sc['status']} · barcode {sc['barcode']}")
            if st.button("Remove", key=f"bc_rm_{sc['uid']}", type="tertiary"):
                state["scans"].remove(sc)
                st.rerun()

    ready = all(sc["name"].strip() for sc in state["scans"]) and state["store"].strip()
    if st.button("Done scanning", type="primary", width="stretch", disabled=not ready):
        state["stage"], state["review"] = "review", None
        st.rerun()
    if not ready:
        st.caption("Fill in the store and any unknown product names to continue.")


def _add_scan(state, code):
    sc = {"uid": uuid.uuid4().hex[:8], "barcode": code, "info": None, "name": "", "brand": "", "quantity": ""}
    known = records.product_by_barcode(code)
    if known:
        sc.update(name=known["name"], brand=known.get("brand", ""), status="Already in your products")
    else:
        with st.spinner("Looking it up on Open Food Facts…"):
            try:
                info = productinfo.off_by_barcode(code)
                sc.update(info=info, name=info["name"], brand=info["brand"], quantity=info.get("quantity", ""),
                          status="Found on Open Food Facts")
            except productinfo.NotFound:
                sc["status"] = "Not on Open Food Facts; you can add its label later from Needs info"
            except Exception as ex:
                sc["status"] = f"Lookup failed ({type(ex).__name__}); you can add its label later"
    state["scans"].append(sc)


def _scan_text(sc):
    return " ".join(x for x in [sc.get("brand"), sc.get("name"), sc.get("quantity")] if x)


# ---------------------------------------------------------------- say it

def _speak_step(state):
    st.caption("Say what you bought, e.g. “6 avocados, the 2-gallon milk and a salmon fillet from Costco”. "
               "Usual purchases are filled in from your history.")
    nonce = st.session_state.setdefault("vc_nonce", 0)
    audio = recorder.record(f"vc_mic_{nonce}", "Tap and say what you bought")
    typed = st.text_input("Or type it", key=f"vc_text_{nonce}")
    go_typed = st.button("Use typed text", key=f"vc_go_{nonce}", disabled=not typed.strip())
    if not (audio or go_typed):
        return
    with st.spinner("Listening…"):
        try:
            result, _, history_refs = voice.understand(
                voice.model(), audio=audio,
                text=None if audio else typed, mode="add")
        except GeminiError as ex:
            st.error(f"Couldn't understand that: {ex}")
            return
    st.session_state["vc_nonce"] = nonce + 1
    adds = [a for a in result.get("actions", []) if a.get("type") == "add"]
    if not adds:
        st.warning(f"I didn't hear any purchases in: “{result.get('transcript', '')}”. "
                   "For finished or wasted food, use Voice update on the Home screen.")
        return
    start_voice_review(state, result, history_refs)
    st.rerun()


def start_voice_review(state, result, history_refs):
    """Turn 'add' actions from the voice engine into review cards (shared with Voice update)."""
    adds = [a for a in result.get("actions", []) if a.get("type") == "add"]
    cards = [_card_from_add(a, history_refs, result.get("transcript", "")) for a in adds]
    store = next((a.get("store") for a in adds if a.get("store")), "") or records.last_store()
    date = _parse_date(next((a.get("purchased_on") for a in adds if a.get("purchased_on")), "")) \
        or timeutil.now().date()
    state.update(stage="review", interpretation=(voice.model(), result), review={
        "store": store, "date": date, "time": "", "source": "voice", "total_paid": 0.0, "tax": None,
        "items": cards, "skipped": [],
    })


def _card_from_add(a, history_refs, transcript):
    """A review card from one 'add' the voice engine heard, filled in from purchase history."""
    known = {records.norm(i["name"]): i for i in records.items()}
    base = history_refs.get(a.get("based_on") or "", {})
    existing = known.get(records.norm(a.get("item")))
    price = _num(a.get("price"), None)
    if price is None and base:
        price = _num(base.get("price_paid"), None)
    return {
        "uid": uuid.uuid4().hex[:8],
        "item": existing["name"] if existing else (a.get("item") or "").strip(),
        "kind": a.get("kind") if a.get("kind") in KINDS else "Food",
        "unit": base.get("unit") or (a.get("unit") if a.get("unit") in UNITS else "each"),
        "storage": a.get("storage") if a.get("storage") in STORAGE else "Fridge",
        "perishable": bool(a.get("perishable", True)), "emoji": a.get("emoji") or "",
        "product": a.get("product") or base.get("product", ""), "brand": "",
        "units": _num(a.get("units"), None) or _num(base.get("units"), 1.0),
        "price": price, "packaged": a.get("packaged", True) is not False,
        "unclear": price is None, "note": "" if price is not None else "Enter the price you paid.",
        "receipt_text": f"said: {a.get('said') or transcript}",
    }


def _refine_mic(state, review):
    """Mic (and text box) on every review screen: add details you missed or fix mistakes by voice."""
    k = state["k"]
    nonce = st.session_state.setdefault(f"{k}_ref_nonce", 0)
    with st.container(border=True):
        st.markdown("**Missed something?** :gray[Say it: store, date, weight, price, or another item]")
        audio = recorder.record(f"{k}_ref_mic_{nonce}", "Tap to add or fix details")
        typed = st.text_input("Or type it", key=f"{k}_ref_text_{nonce}", label_visibility="collapsed",
                              placeholder="Or type it, e.g. from Costco, 3 days ago, salmon was 2.4 lb $24")
        go_typed = st.button("Apply", key=f"{k}_ref_go_{nonce}", disabled=not typed.strip())
    if not (audio or go_typed):
        return
    with st.spinner("Updating…"):
        try:
            result, history_refs = voice.refine(review, audio=audio, text=None if audio else typed)
        except GeminiError as ex:
            st.error(f"Couldn't understand that: {ex}")
            return

    pending = {}
    if result.get("store"):
        review["store"] = result["store"]
        pending[f"{k}_store"] = result["store"]
    date = _parse_date(result.get("date") or "")
    if date:
        review["date"] = date
        pending[f"{k}_date"] = date
    cards = review["items"]
    for u in result.get("updates", []):
        n = u.get("index")
        if not isinstance(n, int) or not 0 <= n < len(cards):
            continue
        c = cards[n]
        for field in ("item", "product"):
            if u.get(field):
                c[field] = str(u[field])
                pending[f"it_{c['uid']}_{field}"] = c[field]
        if _num(u.get("units"), None) is not None:
            c["units"] = _num(u["units"], 1.0)
            pending[f"it_{c['uid']}_units"] = c["units"]
        if u.get("unit") in UNITS:
            c["unit"] = u["unit"]
            pending[f"it_{c['uid']}_unit"] = c["unit"]
        if _num(u.get("price"), None) is not None:
            c.update(price=_num(u["price"], None), unclear=False, note="")
            pending[f"it_{c['uid']}_price"] = c["price"]
    doomed = {n for n in result.get("removes", []) if isinstance(n, int)}
    review["items"] = [c for n, c in enumerate(cards) if n not in doomed]
    review["items"] += [_card_from_add(a, history_refs, result.get("transcript", ""))
                        for a in result.get("adds", [])]
    # Widgets already drawn this run can't be changed now; reset them on the rerun.
    st.session_state[f"{k}_pending"] = pending
    st.session_state[f"{k}_ref_nonce"] = nonce + 1
    st.toast(f"Updated: {result.get('transcript', '')}"[:120])
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

    k = state["k"]
    # Forget widget values the voice fix changed, so they redraw from the updated cards.
    for key in st.session_state.pop(f"{k}_pending", {}):
        st.session_state.pop(key, None)
    st.subheader("Review")
    review["store"] = st.text_input("Store", value=review["store"], key=f"{k}_store",
                                    help="Known stores: " + (", ".join(records.stores()) or "none yet"))
    review["date"] = st.date_input("Date", value=review["date"], key=f"{k}_date", format="MM/DD/YYYY")
    if k == "ap":
        review["total_paid"] = st.number_input("Total paid on receipt ($)", value=review["total_paid"],
                                               min_value=0.0, step=0.01, format="%.2f", key=f"{k}_total")

    dup = records.find_duplicate_purchase(review["store"], review["date"].isoformat(), review["total_paid"])
    if dup and k == "ap":
        st.warning(f"Looks like a receipt you already saved: {dup['store']}, {dup['purchased_on']}, "
                   f"${dup['receipt_total']}. Save only if this is a different purchase.")

    _refine_mic(state, review)
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
                    if st.button("Add back", key=f"{k}_add_{s['uid']}", type="tertiary"):
                        review["skipped"].remove(s)
                        review["items"].append(_blank_card(s["text"], s["amount"]))
                        st.rerun()

    st.write("")
    st.markdown("**Save this to your Google Sheet?**")
    if st.button(f"Save {len(cards)} items", type="primary", width="stretch", disabled=not cards):
        _save(state)
    if st.button("Discard, don't save", width="stretch", key=f"{k}_discard"):
        if state["draft_id"]:
            records.discard_draft(state["draft_id"])
        _reset(state)
        st.rerun()
    back = {"ap": ("Back to photos", "photos"), "bc": ("Back to scanning", "scan"), "vc": ("Say it again", "speak")}[k]
    if st.button(back[0], type="tertiary"):
        state["stage"], state["review"] = back[1], None
        st.rerun()


def _build_review(state):
    model = sheets.get_settings()["model_receipts"]
    if state["k"] == "ap":
        info = receipts.summarize([r["reading"] for r in state["readings"]])
    else:
        info = {"store": state.get("store", ""), "source_type": "barcode scans",
                "date": timeutil.today_str(), "time": "", "total_paid": None, "tax": None}
        state["merged"] = [{"text": _scan_text(sc), "kind": "item", "amount": None} for sc in state["scans"]]
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
            "packaged": it.get("packaged", True) is not False,
            "receipt_text": " / ".join(str(merged[i].get("text", "")) for i in it.get("lines", [])
                                       if isinstance(i, int) and 0 <= i < len(merged)),
        })
        if state["k"] == "bc":
            scan = next((state["scans"][i] for i in it.get("lines", [])
                         if isinstance(i, int) and 0 <= i < len(state["scans"])), None)
            if scan:
                cards[-1].update(barcode=scan["barcode"], info=scan.get("info"), packaged=True,
                                 product=scan.get("name") or cards[-1]["product"],
                                 brand=scan.get("brand") or cards[-1]["brand"])
            if cards[-1]["price"] is None:
                cards[-1].update(unclear=True, note="Enter the price you paid.")
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
        "source": "barcode" if state["k"] == "bc" else (info.get("source_type") or "printed_receipt"),
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
    total = review["total_paid"] if state["k"] == "ap" else sum(c["price"] for c in review["items"])
    payload = dict(review, date=review["date"].isoformat(), total_paid=round(total, 2) if total else "")
    readings = [("receipt_read", r["model"], r["reading"]) for r in state["readings"]]
    if state["interpretation"]:
        task = {"ap": "receipt_items", "bc": "barcode_items", "vc": "voice_add"}[state["k"]]
        readings.append((task, *state["interpretation"]))
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
    again = {"ap": "Add another receipt", "bc": "Scan more items", "vc": "Add more by voice"}[state["k"]]
    if st.button(again, type="primary", width="stretch"):
        _reset(state)
        st.rerun()
    if st.button("Home", width="stretch"):
        _reset(state)
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
