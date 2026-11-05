"""Reading and writing the app's records in the Sheet."""

import json
import re
import uuid

from grocery import productinfo, sheets, timeutil

def new_id(prefix):
    """A random id, only for temporary rows (drafts)."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def slug(text, limit=40):
    s = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return s[:limit].rstrip("-") or "x"


def readable_id(base, taken):
    """Like company email addresses: the first gets the clean name, later ones -2, -3 …"""
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    taken.add(candidate)
    return candidate


def taken_ids(tab, col):
    return {r.get(col) for r in sheets.read_tab(tab)}


def norm(text):
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def items():
    return sheets.read_tab("items")


def live_batches():
    """Every purchased batch except deleted ones (deleted rows stay in the Sheet so Undo works)."""
    return [b for b in sheets.read_tab("batches") if b.get("status") != "deleted"]


def stores():
    """Distinct store names seen so far."""
    names = []
    for b in live_batches():
        if b.get("store") and b["store"] not in names:
            names.append(b["store"])
    return names


def open_batches_by_item():
    out = {}
    for b in sheets.read_tab("batches"):
        if b.get("status") == "open":
            out.setdefault(b.get("item_id"), []).append(b)
    return out


def find_duplicate_purchase(store, date, total):
    """An earlier purchase with the same store, date and total, if any."""
    if not (store and date and total not in (None, "", 0, 0.0)):
        return None
    for p, _ in purchases():
        try:
            same_total = abs(float(p.get("receipt_total") or "nan") - float(total)) < 0.005
        except ValueError:
            same_total = False
        if norm(p.get("store")) == norm(canonical_store(store)) and p.get("purchased_on") == str(date) and same_total:
            return p
    return None


def save_purchase(review, finished, draft_id, readings):
    """Write one reviewed receipt to the Sheet.

    review   = {"store", "date", "time", "source", "total_paid", "items": [card dicts]}
    finished = {item_id: "YYYY-MM-DD"} open batches the user said are finished
    readings = [(task, model, json-able reading)] kept in ai_readings
    """
    now = timeutil.now_str()
    store = {"name": canonical_store(review["store"])}

    known = {norm(i["name"]): i for i in items()}
    item_ids = taken_ids("items", "item_id")
    new_items = []
    for card in review["items"]:
        if norm(card["item"]) not in known:
            row = {
                "item_id": readable_id(slug(card["item"]), item_ids), "name": card["item"].strip(),
                "kind": card["kind"],
                "perishable": card["perishable"] if card["kind"] != "One-time" else False,
                "unit": card["unit"], "storage": card["storage"], "emoji": card.get("emoji", ""),
                "tracked": card["kind"] != "One-time", "created_at": now,
                "photo_url": item_picture(card.get("wiki"), card["item"]),
            }
            known[norm(row["name"])] = {k: sheets._cell(v) for k, v in row.items()}
            new_items.append(row)
    sheets.append_rows("items", new_items)
    # A known item kept somewhere new (e.g. "I keep raspberries in the freezer") updates the item.
    item_changes, new_ids = {}, {r["item_id"] for r in new_items}
    for card in review["items"]:
        item = known[norm(card["item"])]
        if item["item_id"] in new_ids or card["kind"] == "One-time":
            continue
        change = {}
        if card.get("storage") and card["storage"] != item.get("storage"):
            change["storage"] = card["storage"]
        perishable = "yes" if card.get("perishable") else "no"
        if item.get("perishable") and perishable != item["perishable"]:
            change["perishable"] = perishable
        if change:
            item_changes[item["item_id"]] = change
            item.update(change)
    sheets.update_rows("items", "item_id", item_changes)

    existing_products = sheets.read_tab("products")
    product_ids = {p["product_id"] for p in existing_products}
    products = {(p["item_id"], norm(p["name"])): p for p in existing_products}
    by_barcode = {p["barcode"]: p for p in existing_products if p.get("barcode")}
    new_products, info_updates = [], {}
    for card in review["items"]:
        item = known[norm(card["item"])]
        key = (item["item_id"], norm(card["product"]))
        found = by_barcode.get(card.get("barcode")) or products.get(key)
        if found:
            products[key] = found
            if card.get("info") and found.get("info_status") != "confirmed":
                info_updates[found["product_id"]] = dict(productinfo.product_columns(card["info"]),
                                                         barcode=card.get("barcode", ""))
        elif card["product"].strip():
            row = {"product_id": readable_id(slug(card["product"], 50), product_ids),
                   "item_id": item["item_id"], "item": item["name"],
                   "brand": card.get("brand", ""), "name": card["product"].strip(),
                   "barcode": card.get("barcode", ""), "packaged": card.get("packaged", True),
                   "source": review["source"], "created_at": now,
                   "details": f"{card['units']:g} {card['unit']} at {store['name']}; "
                              f"{card.get('receipt_text', '')}".strip("; ")}
            if card.get("info"):
                row.update(productinfo.product_columns(card["info"]))
            products[key] = row
            new_products.append(row)
    sheets.append_rows("products", new_products)
    sheets.update_rows("products", "product_id", info_updates)
    for card in review["items"]:
        said = (card.get("said_name") or "").strip()
        product = products.get((known[norm(card["item"])]["item_id"], norm(card["product"])))
        if said and product and norm(said) not in (norm(product["name"]), norm(card["item"])):
            add_nicknames(product["product_id"], [said])

    existing_batches = sheets.read_tab("batches")
    purchase_id = readable_id(f"{slug(store['name'], 20)}-{review['date']}",
                              {b.get("purchase_id") for b in existing_batches})
    batch_ids = {b.get("batch_id") for b in existing_batches}
    batches = []
    for card in review["items"]:
        item = known[norm(card["item"])]
        product = products.get((item["item_id"], norm(card["product"])), {})
        batches.append({
            "batch_id": readable_id(f"{item['item_id']}-{review['date']}", batch_ids),
            "purchase_id": purchase_id, "item_id": item["item_id"],
            "item": item["name"], "product_id": product.get("product_id", ""),
            "product": product.get("name", ""), "store": store["name"], "purchased_on": review["date"],
            "purchased_time": review.get("time", ""), "source": review["source"],
            "units": card["units"], "unit": card["unit"], "price_paid": card["price"],
            "receipt_total": review.get("total_paid", ""),
            "expiry_date": card["expiry_date"].isoformat() if card.get("expiry_date") else "",
            "status": "open" if card["kind"] != "One-time" else "not tracked", "created_at": now,
            "packs": _packs_of(card, product),
        })
    sheets.append_rows("batches", batches)

    if finished:
        from grocery import events  # local import: events imports this module
        new_ids = {b["batch_id"] for b in batches}
        open_now = open_batches_by_item()
        events.apply([
            {"type": "finish", "batch": b, "happened_on": day}
            for item_id, day in finished.items() for b in open_now.get(item_id, [])
            if b["batch_id"] not in new_ids
        ], said=f"Finished when restocking at {store['name']}", source="receipt review")

    reading_ids = taken_ids("ai_readings", "reading_id")
    sheets.append_rows("ai_readings", [
        {"reading_id": readable_id(f"{purchase_id}-{slug(task)}", reading_ids), "purchase_id": purchase_id,
         "store": store["name"], "at": now, "task": task, "model": model,
         "reading": json.dumps(data, ensure_ascii=False)}
        for task, model, data in readings
    ])
    if draft_id:
        sheets.delete_rows_where("drafts", draft_id=draft_id)
    return purchase_id, len(batches)


def _packs_of(card, product):
    """Packs as sold. An amount said in units ("1 gallon") of a product with a known pack size is
    stored as its share of a pack, so a later pack-size lookup knows it was not "1 pack"."""
    if card.get("packs") is not None:
        return card["packs"]
    size = _float(product.get("pack_size"))
    return round(float(card["units"]) / size, 6) if size and card.get("units") else ""


def save_draft(draft_id, photo_no, photo_name, model, reading):
    sheets.append_rows("drafts", [{
        "draft_id": draft_id, "photo_no": photo_no, "photo_name": photo_name,
        "created_at": timeutil.now_str(), "model": model,
        "reading": json.dumps(reading, ensure_ascii=False),
    }])


def load_drafts():
    """{draft_id: {"created_at", "readings": [(photo_no, name, model, reading)]}}"""
    out = {}
    for row in sheets.read_tab("drafts"):
        try:
            reading = json.loads(row["reading"])
        except (ValueError, KeyError):
            continue
        d = out.setdefault(row["draft_id"], {"created_at": row["created_at"], "readings": []})
        d["readings"].append((int(row["photo_no"] or 0), row["photo_name"], row["model"], reading))
    for d in out.values():
        d["readings"].sort(key=lambda r: r[0])
    return out


def discard_draft(draft_id, photo_no=None):
    if photo_no is None:
        sheets.delete_rows_where("drafts", draft_id=draft_id)
    else:
        sheets.delete_rows_where("drafts", draft_id=draft_id, photo_no=photo_no)


def products_needing_info():
    """Packaged products still missing something, newest first.

    Each gets "missing": "size" when it was only ever counted in packs (or saved as 1) with no known
    pack size, "update" when the pack size is known but purchases still need it applied (with
    "resize" = [(batch, new units)]), "ingredients" for foods with no label info yet.
    """
    tracked = {i["item_id"]: i for i in items() if i.get("kind") in ("Food", "Household")}
    batches = live_batches()
    out = []
    for p in sheets.read_tab("products"):
        item = tracked.get(p["item_id"])
        if not item or p.get("packaged") == "no":
            continue
        mine = [b for b in batches if b.get("product_id") == p["product_id"]]
        if not mine:
            continue  # nothing bought (or all of it deleted): nothing to fill in now
        missing = info_gaps(p, item, mine)
        resize = _to_resize(mine, _float(p.get("pack_size")), p.get("pack_unit"))
        if resize:
            missing.insert(-1 if "ingredients" in missing else len(missing), "update")  # size known, purchases say "1"
        if missing:
            out.append(dict(p, emoji=item.get("emoji", ""), missing=missing, item_row=item, resize=resize))
    return sorted(out, key=lambda p: p.get("created_at", ""), reverse=True)


def info_gaps(product, item, batches):
    """What a packaged product still lacks: "size" (only ever counted in packs, size unknown) and/or
    "ingredients" (a food with no label info yet)."""
    gaps = []
    if not product.get("pack_size") and batches and all(_waiting_for_pack_size(b) for b in batches):
        gaps.append("size")
    if (item.get("kind") == "Food" and product.get("info_status") not in ("confirmed", "none")
            and not product.get("ingredients")):
        gaps.append("ingredients")
    return gaps


def _brand_if_missing(product_id, info):
    """A lookup's brand, only when the product has none yet (a brand you typed always stays)."""
    product = next((p for p in sheets.read_tab("products") if p["product_id"] == product_id), {})
    brand = str(info.get("brand") or "").strip()
    return {"brand": brand} if brand and not str(product.get("brand") or "").strip() else {}


def save_product_info(product_id, info, barcode=""):
    changes = productinfo.product_columns(info)
    changes.update(_brand_if_missing(product_id, info))
    if barcode:
        changes["barcode"] = barcode
    sheets.update_rows("products", "product_id", {product_id: changes})


def mark_no_info(product_id, packaged=True):
    """The user says this product has no label to read (or isn't packaged)."""
    changes = {"info_status": "none"}
    if not packaged:
        changes["packaged"] = "no"
    sheets.update_rows("products", "product_id", {product_id: changes})


def product_by_barcode(barcode):
    return next((p for p in sheets.read_tab("products") if p.get("barcode") == barcode), None)


def last_store():
    rows = live_batches()
    return rows[-1]["store"] if rows else ""


def purchase_history(limit=60):
    """Most recent batch per (item, store, product), newest first, for voice quick-add."""
    seen, out = set(), []
    rows = live_batches()
    for b in sorted(rows, key=lambda b: (b.get("purchased_on", ""), b.get("created_at", "")), reverse=True):
        key = (b.get("item_id"), norm(b.get("store")), norm(b.get("product")))
        if key in seen:
            continue
        seen.add(key)
        out.append(b)
        if len(out) >= limit:
            break
    return out


# Big chains whose own sites list what they sell; small local stores add nothing to a search.
BIG_CHAINS = ["Costco", "Sam's Club", "Walmart", "Target", "Kroger", "Safeway", "Albertsons", "Randalls", "H-E-B",
              "Whole Foods", "Trader Joe's", "Aldi", "Sprouts", "Central Market", "Fiesta"]


def big_chain(store):
    s = norm(store).replace("'", "").replace("-", "")
    return next((c for c in BIG_CHAINS if norm(c).replace("'", "").replace("-", "") in s), None)


def product_search_text(p):
    """Short search text: brand + product + real size, plus the store only if it's a big chain."""
    name = p["name"].strip()
    parts = [] if norm(p.get("brand")) and norm(p.get("brand")) in norm(name) else [p.get("brand", "")]
    parts.append(name)
    size = _float(p.get("pack_size"))
    if size and p.get("pack_unit") not in ("", "each") and not re.search(r"\d", name):
        parts.append(f"{size:g} {p['pack_unit']}")
    latest = max((b for b in live_batches() if b.get("product_id") == p["product_id"]),
                 key=lambda b: b.get("purchased_on", ""), default={})
    chain = big_chain(latest.get("store"))
    if chain and norm(chain) not in norm(name):
        parts.append(chain)
    return " ".join(x.strip() for x in parts if x and x.strip())


def save_product_match(product_id, info):
    """The web found the exact product but no label info: remember the match, stop asking."""
    product = next((p for p in sheets.read_tab("products") if p["product_id"] == product_id), {})
    details = "; ".join(x for x in [product.get("details", ""), f"Matched online: {info.get('name', '')}"] if x)
    sheets.update_rows("products", "product_id", {product_id: {
        "info_status": "none", "info_source": "Web search", "info_url": info.get("url", ""), "details": details,
        **({"photo_url": info["photo_url"]} if info.get("photo_url") else {}),
        **({"barcode": info["barcode"]} if info.get("barcode") else {}),
        **_brand_if_missing(product_id, info),
    }})


def canonical_store(name):
    """Costco and Costco Business Center sell the same products: one store, "Costco"."""
    name = re.sub(r"\s+", " ", str(name or "")).strip()
    return "Costco" if "costco" in name.lower() else name


def purchases():
    """Purchases rebuilt from batches: [(summary dict, [batch rows])], newest first."""
    groups = {}
    for b in live_batches():
        groups.setdefault(b.get("purchase_id"), []).append(b)
    out = []
    for pid, rows in groups.items():
        first = rows[0]
        out.append(({
            "purchase_id": pid, "store": first.get("store", ""), "purchased_on": first.get("purchased_on", ""),
            "purchased_time": first.get("purchased_time", ""), "source": first.get("source", ""),
            "receipt_total": first.get("receipt_total", ""), "created_at": first.get("created_at", ""),
        }, rows))
    return sorted(out, key=lambda p: p[0]["created_at"], reverse=True)


def recent_purchases(limit=20):
    return purchases()[:limit]


def products():
    return sheets.read_tab("products")


def set_item(item_id, **fields):
    """Change how an item is kept (storage, perishable…); applies to it from now on."""
    sheets.update_rows("items", "item_id", {item_id: fields})


def rename_product(product_id, name, brand):
    """Fix a product's name or brand; its purchases show the new name too."""
    name = re.sub(r"\s+", " ", name).strip()
    sheets.update_rows("products", "product_id", {product_id: {"name": name, "brand": brand.strip()}})
    sheets.update_rows("batches", "batch_id", {b["batch_id"]: {"product": name} for b in sheets.read_tab("batches")
                                               if b.get("product_id") == product_id})


def save_ingredients(product_id, ingredients, source="Said by you"):
    sheets.update_rows("products", "product_id", {product_id: {
        "ingredients": ingredients, "info_status": "confirmed", "info_source": source}})


def fill_from_lookup(product_id, info, use_photo=True):
    """A redo search on the store's site: fill only what's still empty (and the photo, if you want it).
    Returns the names of the fields that were filled."""
    product = next((p for p in sheets.read_tab("products") if p["product_id"] == product_id), {})
    cols = productinfo.product_columns(info)
    change = {k: v for k, v in cols.items()
              if k not in ("info_status", "info_source", "info_url", "photo_url", "pack_size", "pack_unit", "pack_count")
              and v not in ("", None) and not product.get(k)}
    if info.get("photo_url") and (use_photo or not product.get("photo_url")):
        change["photo_url"] = info["photo_url"]
    if info.get("barcode") and not product.get("barcode"):
        change["barcode"] = info["barcode"]
    change.update(_brand_if_missing(product_id, info))
    if change.get("ingredients") or change.get("nutrients_json"):
        change.update(info_status="confirmed", info_source=info.get("source", "Web search"), info_url=info.get("url", ""))
    elif info.get("url") and not product.get("info_url"):
        change["info_url"] = info["url"]
    sheets.update_rows("products", "product_id", {product_id: change})
    filled = [k for k in change if k not in ("info_status", "info_source")]
    if info.get("pack_units") and not _float(product.get("pack_size")):
        apply_pack_size(product_id, info["pack_units"], info.get("pack_unit") or "each", count=info.get("pack_count"))
        filled.append("pack size")
    return filled


def mark_no_nutrition(product_id):
    """You checked: no nutrition label worth tracking (e.g. a spice). Counts as done."""
    sheets.update_rows("products", "product_id", {product_id: {"nutrition_status": "none"}})


def completeness(product, item, batch):
    """[(what, filled?)] for the profile bar: what's known about this purchase's product and item."""
    item, batch = item or {}, batch or {}
    packaged = bool(product) and product.get("packaged") != "no"
    photo = (product or {}).get("photo_url") if packaged else item.get("photo_url") not in ("", "none", None)
    checks = [("photo", bool(photo)), ("kept in", bool(item.get("storage")))]
    if item.get("perishable") == "yes":
        checks.append(("expiry", bool(batch.get("expiry_date"))))
    if packaged:
        no_label = product.get("info_status") == "none"
        checks += [
            ("brand", bool(product.get("brand"))),
            ("pack size", bool(_float(product.get("pack_size")))),
            ("ingredients", bool(product.get("ingredients")) or no_label),
            ("nutrition", bool(product.get("nutrients_json")) or no_label or product.get("nutrition_status") == "none"),
            ("nickname", bool(nicknames(product))),
        ]
    return checks


MERGE_FILL = ("photo_url", "barcode", "brand", "serving_size", "servings_per_pack", "ingredients", "nutrients_json",
              "abv", "info_source", "info_url", "info_status", "pack_size", "pack_unit", "packaged")


def merge_preview(from_id, into_id):
    """What merging one product into another would do: purchases moved, and what it brings along."""
    rows = {p["product_id"]: p for p in products()}
    a, b = rows[from_id], rows[into_id]
    moved = [x for x in sheets.read_tab("batches") if x.get("product_id") == from_id]
    brings = [col for col in MERGE_FILL if a.get(col) and not b.get(col)]
    new_names = [n for n in nicknames(a) if norm(n) not in {norm(m) for m in nicknames(b)} | {norm(b["name"])}]
    return {"from": a, "into": b, "moved": moved, "brings": brings, "nicknames": new_names}


def merge_products(from_id, into_id):
    """Two rows that are the same product: keep `into`, move every purchase of `from` to it, fill its
    empty details (photo, info, pack size, barcode…) from `from`, add its nicknames, then drop `from`."""
    plan = merge_preview(from_id, into_id)
    a, b = plan["from"], plan["into"]
    change = {col: a[col] for col in plan["brings"]}
    if plan["nicknames"]:
        change["nicknames"] = "; ".join(nicknames(b) + plan["nicknames"])
    sheets.update_rows("products", "product_id", {into_id: change})
    sheets.update_rows("batches", "batch_id", {
        x["batch_id"]: {"product_id": into_id, "product": b["name"], "item_id": b["item_id"], "item": b.get("item", "")}
        for x in plan["moved"]})
    sheets.remove_rows("products", lambda r: r.get("product_id") == from_id)
    size = _float(b.get("pack_size") or change.get("pack_size"))
    if size:  # purchases of the merged product that were waiting for a pack size get it now
        apply_pack_size(into_id, size, b.get("pack_unit") or change.get("pack_unit") or "")
    return plan


def nicknames(product):
    return [n.strip() for n in str(product.get("nicknames", "")).split(";") if n.strip()]


def add_nicknames(product_id, phrases):
    p = next((p for p in products() if p["product_id"] == product_id), None)
    if not p:
        return
    have = nicknames(p)
    lowered = {norm(n) for n in have} | {norm(p["name"])}
    for phrase in phrases:
        phrase = re.sub(r"\s+", " ", str(phrase)).strip()
        if phrase and norm(phrase) not in lowered:
            have.append(phrase)
            lowered.add(norm(phrase))
    sheets.update_rows("products", "product_id", {product_id: {"nicknames": "; ".join(have)}})


def remove_nickname(product_id, phrase):
    p = next((p for p in products() if p["product_id"] == product_id), None)
    if p:
        keep = [n for n in nicknames(p) if norm(n) != norm(phrase)]
        sheets.update_rows("products", "product_id", {product_id: {"nicknames": "; ".join(keep)}})


def set_photo(product_id, photo):
    sheets.update_rows("products", "product_id", {product_id: {"photo_url": photo}})


def products_prompt_lines(limit=80):
    """Known products with nicknames, for the voice AI: ref | item | product | nicknames."""
    rows = sorted(products(), key=lambda p: p.get("created_at", ""), reverse=True)[:limit]
    return {f"p{n}": p for n, p in enumerate(rows, start=1)}


def item_picture(*titles):
    """Generic item picture URL, "none" if Wikipedia has none, "" to retry later."""
    try:
        return productinfo.wiki_image(*titles) or "none"
    except productinfo.TryLater:
        return ""


def fill_missing_item_pictures(limit=3):
    """Look up pictures for items saved before pictures existed (a few per visit)."""
    todo = [i for i in items() if not i.get("photo_url")][:limit]
    changes = {}
    for i in todo:
        pic = item_picture(i["name"])
        if not pic:
            break  # Wikipedia is busy: try again next time
        changes[i["item_id"]] = {"photo_url": pic}
    sheets.update_rows("items", "item_id", changes)


def set_item_picture(item_id, url):
    sheets.update_rows("items", "item_id", {item_id: {"photo_url": url}})


def _waiting_for_pack_size(batch):
    """Saved before its pack size was known: counted in packs (units = packs), or an old plain "1"."""
    packs, units = _float(batch.get("packs")), _float(batch.get("units"))
    return units == packs if packs is not None else units in (None, 1.0)


def _to_resize(batches, pack_size, unit):
    """[(batch, new units)] for purchases still waiting for this pack size, where it changes something."""
    if not pack_size:
        return []
    out = []
    for b in batches:
        if _waiting_for_pack_size(b):
            new = (_float(b.get("packs")) or 1.0) * pack_size
            if new != _float(b.get("units")) or (unit and unit != b.get("unit")):
                out.append((b, new))
    return out


def lookup_store(product):
    """The big chain whose own site should be searched first: from a store brand (Kirkland = Costco)
    or where it was bought; "" for imported / local-store products."""
    by_brand = store_from_brands([product.get("brand", ""), product.get("name", "")])
    if by_brand:
        return by_brand
    latest = max((b for b in live_batches() if b.get("product_id") == product.get("product_id")),
                 key=lambda b: b.get("purchased_on", ""), default={})
    return big_chain(latest.get("store")) or ""


def apply_pack_size(product_id, pack_units, unit, fix_batches=True, count=None):
    """Remember a product's pack size; purchases counted in packs become packs x pack size.

    Returns [(batch, new units)] for the purchases that changed. Pantry, Insights, lifespans and
    prices are all worked out from these rows, so they follow automatically.
    """
    change = {"pack_size": pack_units, "pack_unit": unit}
    if count:
        change["pack_count"] = count
    sheets.update_rows("products", "product_id", {product_id: change})
    if not fix_batches:
        return []
    mine = [b for b in sheets.read_tab("batches") if b.get("product_id") == product_id]
    resize = _to_resize(mine, pack_units, unit)
    sheets.update_rows("batches", "batch_id", {
        b["batch_id"]: {"units": new, "unit": unit or b.get("unit"),
                        **({} if _float(b.get("packs")) is not None else {"packs": 1})}
        for b, new in resize})
    return resize


def keep_amounts(product_id):
    """The user says purchases saved as "1" are right as they are: record them as part of a pack."""
    product = next((p for p in sheets.read_tab("products") if p["product_id"] == product_id), {})
    size = _float(product.get("pack_size"))
    mine = [b for b in sheets.read_tab("batches") if b.get("product_id") == product_id]
    sheets.update_rows("batches", "batch_id", {
        b["batch_id"]: {"packs": round((_float(b.get("units")) or 0) / size, 6)}
        for b, _ in _to_resize(mine, size, product.get("pack_unit"))})


def item_unit(item_id):
    return next((i.get("unit") or "each" for i in items() if i["item_id"] == item_id), "each")


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


STORE_BRANDS = {  # store brands that tell us where something was bought
    "kirkland": "Costco", "great value": "Walmart", "equate": "Walmart", "marketside": "Walmart",
    "signature select": "Safeway", "o organics": "Safeway", "h-e-b": "H-E-B", "heb": "H-E-B",
    "hill country fare": "H-E-B", "trader joe": "Trader Joe's", "365 by whole foods": "Whole Foods",
    "member's mark": "Sam's Club", "good & gather": "Target", "simple truth": "Kroger",
}


def store_from_brands(texts):
    """'Kirkland …' means Costco, 'Great Value …' means Walmart, and so on; "" if no clue."""
    for text in texts:
        low = str(text).lower()
        for brand, store in STORE_BRANDS.items():
            if brand in low:
                return store
    return ""


def mark_size_ok(product_id):
    """Stop asking for a pack size (sold by weight, or the saved quantity is already right)."""
    latest = [b for b in live_batches() if b.get("product_id") == product_id]
    size = latest[-1].get("units") if latest else "1"
    unit = latest[-1].get("unit") if latest else "each"
    sheets.update_rows("products", "product_id", {product_id: {"pack_size": size or "1", "pack_unit": unit}})
    keep_amounts(product_id)
