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
            if card.get("picture") and not found.get("photo_url"):  # picture from an online order
                info_updates.setdefault(found["product_id"], {})["photo_url"] = card["picture"]
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
            if card.get("picture") and not row.get("photo_url"):  # the store's own picture, from an online order
                row["photo_url"] = card["picture"]
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
    if (not product.get("pack_size") and product.get("pack_varies") != "yes" and batches
            and all(_waiting_for_pack_size(b) for b in batches)):
        gaps.append("size")
    if (item.get("kind") == "Food" and product.get("info_status") not in ("confirmed", "none")
            and not product.get("ingredients")):
        gaps.append("ingredients")
    return gaps


def _brand_if_missing(product_id, info):
    """A lookup's brand, only when the product has none yet (a brand you typed always stays)."""
    product = next((p for p in sheets.read_tab("products") if p["product_id"] == product_id), {})
    brand = str(info.get("brand") or "").strip()
    if product.get("no_brand") == "yes":
        return {}  # you said it has no brand
    return {"brand": brand} if brand and not str(product.get("brand") or "").strip() else {}


def save_product_info(product_id, info, barcode=""):
    """Save what a lookup or label photo found. Only what it actually found is written: a photo of just the
    nutrition panel never erases ingredients saved earlier, and the other way round."""
    changes = productinfo.product_columns(info)
    for col in ("serving_size", "servings_per_pack", "ingredients", "nutrients_json", "abv"):
        if changes.get(col) in ("", None):
            changes.pop(col, None)
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


def set_purchase_date(purchase_id, day):
    """Fix the date of a whole receipt (e.g. read as the wrong year): every item on it moves to that day, so
    days open, lifespans and warnings are all worked out from the right date."""
    day = day.isoformat() if hasattr(day, "isoformat") else str(day)
    sheets.update_rows("batches", "batch_id", {b["batch_id"]: {"purchased_on": day}
                                               for b in sheets.read_tab("batches") if b.get("purchase_id") == purchase_id})


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


_SIZE = re.compile(r"[,(]?\s*(\d+(\.\d+)?\s*(x|×)\s*)?\d+(\.\d+)?\s*-?\s*(fl\.?\s*oz|oz|lbs?|lb|g|kg|ml|l|gal|ct|count|pack|pk|rolls?|pieces?)\b\.?\)?", re.I)


def short_name(product, item=None):
    """What a product is, without brand or size: "Shan Chicken White Karahi Masala, 50 g" -> "Chicken White Karahi
    Masala". Falls back to the item's name when the product isn't more specific."""
    item_name = (item or {}).get("name") or (product or {}).get("item", "")
    if not product:
        return item_name
    name = str(product.get("name") or "")
    brands = {str(product.get("brand") or ""), "Kirkland Signature", *[b.title() for b in STORE_BRANDS]}
    for brand in sorted(brands, key=len, reverse=True):
        if brand and name.lower().startswith(brand.lower() + " "):
            name = name[len(brand):].strip()
            break
    name = _SIZE.sub("", name)
    name = re.sub(r"\((frozen|fresh)\)", "", name, flags=re.I)
    name = re.sub(r"\s+", " ", name).strip(" ,-–")
    return name or item_name


def needs_own_item(product, item):
    """The exact name says more than a generic item ("Spice mix" for a Chicken White Karahi masala)."""
    def stems(text):
        return {w[:-3] + "y" if w.endswith("ies") else w[:-2] if w.endswith("oes") else w.rstrip("s")
                for w in re.findall(r"[a-z0-9]+", str(text).lower())}
    exact = short_name(product, item)
    return bool(exact) and not stems(item.get("name", "")) <= stems(exact)


def move_to_item(product_id, item_name):
    """Give this product (and its purchases) its own item, e.g. one masala out of "Spice mix", so its shelf life
    is learned on its own. Joins an item with that name if you already have one. The old item keeps its other
    products; it's removed only if nothing uses it any more."""
    item_name = re.sub(r"\s+", " ", item_name).strip()
    product = next((p for p in sheets.read_tab("products") if p["product_id"] == product_id), None)
    if not product or not item_name:
        return None
    old = next((i for i in items() if i["item_id"] == product.get("item_id")), {})
    target = next((i for i in items() if norm(i["name"]) == norm(item_name)), None)
    if target is None:
        target = dict(old, item_id=readable_id(slug(item_name), taken_ids("items", "item_id")), name=item_name,
                      created_at=timeutil.now_str(), photo_url="", no_expiry="")
        sheets.append_rows("items", [target])
    sheets.update_rows("products", "product_id", {product_id: {"item_id": target["item_id"], "item": target["name"]}})
    sheets.update_rows("batches", "batch_id", {b["batch_id"]: {"item_id": target["item_id"], "item": target["name"]}
                                               for b in sheets.read_tab("batches") if b.get("product_id") == product_id})
    still_used = any(b.get("item_id") == old.get("item_id") for b in sheets.read_tab("batches")) or \
        any(p.get("item_id") == old.get("item_id") for p in sheets.read_tab("products"))
    if old and not still_used:
        sheets.remove_rows("items", lambda r: r.get("item_id") == old["item_id"])
    return target["item_id"]


GROUP_PROMPT = """You sort grocery products into GROUPS for a home pantry app.
A group is a set of products the user uses interchangeably, whatever the brand: all sausages are "Sausages",
all mangoes "Mango", all parathas "Paratha", plain and alkaline water "Water", all eggs "Eggs".
But products that are NOT swapped for each other are each their own group, even with similar names:
each different masala / spice mix, chutney, pickle or sauce flavour gets its own group named by its variety
(e.g. "Chicken Karahi Masala", "Biryani Masala", "Mint Chutney"); plain single spices are their own group too
("Turmeric Powder"). Household things work the same way ("Paper Towels", "Dish Soap").
Group names are short, plural where natural, without brand or size.
Existing groups (prefer one of these, spelled exactly, when it fits):
{groups}
Products (ref | current group | brand | product name):
{products}
Return only JSON: {{"ref": "group name", ...}} with one entry per product ref."""


def groups():
    """Group names in use: an item that at least one product or live purchase is in. A group emptied by moving
    its last product away disappears at once (a deleted purchase may still point to it, only for Undo)."""
    used = {p.get("item_id") for p in products()} | {b.get("item_id") for b in live_batches()}
    return sorted({i["name"] for i in items() if i.get("name") and i["item_id"] in used}, key=str.lower)


def suggest_groups(model, limit=80):
    """One AI call that suggests a group for every product whose group you haven't confirmed and that has no
    suggestion yet. Saved on the product (group_suggestion), so it's asked once per product."""
    from grocery import gemini
    rows = [p for p in products() if p.get("group_status") != "ok" and not p.get("group_suggestion")][:limit]
    if not rows:
        return 0
    names = groups()
    refs = {f"p{n}": p for n, p in enumerate(rows, start=1)}
    lines = "\n".join(f"{ref} | {p.get('item', '')} | {p.get('brand', '')} | {p['name']}" for ref, p in refs.items())
    try:
        reply = gemini.generate_json("group_suggest", model, GROUP_PROMPT.format(
            groups="\n".join(names) or "(none yet)", products=lines))
    except Exception:
        return 0
    known = {norm(g): g for g in names}
    change = {}
    for ref, p in refs.items():
        name = reply.get(ref) if isinstance(reply, dict) else None
        if isinstance(name, str) and name.strip():
            name = re.sub(r"\s+", " ", name).strip()
            change[p["product_id"]] = {"group_suggestion": known.get(norm(name), name)}
    if change:
        sheets.update_rows("products", "product_id", change)
    return len(change)


def set_group(product_id, group_name):
    """Put a product in a group (an existing one, or a new one by that name) and mark its group as confirmed.
    Products in one group share one 'how long it lasts' and are counted together in Insights."""
    product = next((p for p in products() if p["product_id"] == product_id), None)
    if not product or not str(group_name).strip():
        return None
    if norm(group_name) != norm(product.get("item", "")):
        move_to_item(product_id, group_name)
    sheets.update_rows("products", "product_id", {product_id: {"group_status": "ok"}})
    return group_name


def save_ingredients(product_id, ingredients, source="Said by you"):
    sheets.update_rows("products", "product_id", {product_id: {
        "ingredients": ingredients, "info_status": "confirmed", "info_source": source}})


LABEL_COLS = ("serving_size", "servings_per_pack", "ingredients", "nutrients_json", "abv")


def fill_from_lookup(product_id, info, use_photo=True, use_pack=False, new_name=None, new_brand=None, replace=(),
                     new_item=None):
    """Save a lookup / scan match: fill only what's still empty (and the photo, if you want it). What a photo of
    the label read replaces earlier label info, since the label always wins. new_name / new_brand: the exact
    name and brand it identified, when you chose to use them. Returns the names of the fields that changed."""
    product = next((p for p in sheets.read_tab("products") if p["product_id"] == product_id), {})
    cols = productinfo.product_columns(info)
    from_photo = info.get("from_photo") or (list(LABEL_COLS) if info.get("source") == "Label photo" else [])
    change = {k: v for k, v in cols.items()
              if k not in ("info_status", "info_source", "info_url", "photo_url", "pack_size", "pack_unit", "pack_count")
              and v not in ("", None) and (not product.get(k) or k in from_photo or k in replace
                                           or (k in ("serving_size", "servings_per_pack") and "nutrients_json" in replace))}
    if "nutrients_json" in change:
        change["nutrition_status"] = ""  # real values replace an earlier "no nutrition"
    if info.get("photo_url") and (use_photo or not product.get("photo_url")):
        change["photo_url"] = info["photo_url"]
    if info.get("barcode") and not product.get("barcode"):
        change["barcode"] = info["barcode"]
    change.update(_brand_if_missing(product_id, info))
    if product.get("lookup_missed_on"):
        change["lookup_missed_on"] = ""  # found after all
    if change.get("ingredients") or change.get("nutrients_json"):
        change.update(info_status="confirmed", info_source=info.get("source", "Web search"), info_url=info.get("url", ""))
    elif info.get("url") and not product.get("info_url"):
        change["info_url"] = info["url"]
    sheets.update_rows("products", "product_id", {product_id: change})
    filled = [k for k in change if k not in ("info_status", "info_source")]
    if new_name or new_brand:
        rename_product(product_id, new_name or product.get("name", ""),
                       new_brand if new_brand is not None else change.get("brand", product.get("brand", "")))
        filled.append("name")
    if new_item:
        move_to_item(product_id, new_item)
        filled.append("item")
    if info.get("pack_units") and (use_pack or not _float(product.get("pack_size"))):
        apply_pack_size(product_id, info["pack_units"], info.get("pack_unit") or "each", count=info.get("pack_count"),
                        recount=use_pack)
        filled.append("pack size")
    return filled


def set_pack_varies(product_id, varies):
    """Sold by weight (salmon, a pack of chicken): no fixed pack size, each purchase records its own weight."""
    sheets.update_rows("products", "product_id", {product_id: {"pack_varies": "yes" if varies else ""}})


def set_no_brand(product_id, none):
    sheets.update_rows("products", "product_id", {product_id: {"no_brand": "yes" if none else "", **({"brand": ""} if none else {})}})


def set_no_expiry(item_id, none):
    sheets.update_rows("items", "item_id", {item_id: {"no_expiry": "yes" if none else ""}})


def set_barcode(product_id, barcode):
    """A scanned barcode is a fact about the product: save it straight away (it survives an app reload).
    It's a new way to find it, so an earlier "not found" no longer holds it back."""
    sheets.update_rows("products", "product_id", {product_id: {"barcode": barcode, "lookup_missed_on": ""}})


LOOKUP_NEEDS = {"photo": "photo", "brand": "brand", "pack size": "pack", "ingredients": "ingredients",
                "nutrition": "nutrition"}
MISS_DAYS = 30


def lookup_needs(product, item):
    """What a lookup could still fill for this product: photo, brand, pack, ingredients, nutrition.
    Loose food gets its nutrition from USDA (free), so that's never searched for."""
    checks = completeness(product, item, {})
    packaged = bool(product) and product.get("packaged") != "no"
    return {LOOKUP_NEEDS[what] for what, ok in checks
            if not ok and what in LOOKUP_NEEDS and (packaged or what != "nutrition")}


def set_lookup_missed(product_id, missed=True):
    """Remember that Look up all found nothing today (or clear it after a find)."""
    sheets.update_rows("products", "product_id",
                       {product_id: {"lookup_missed_on": timeutil.now().date().isoformat() if missed else ""}})


def missed_recently(product):
    """True for 30 days after Look up all found nothing for it."""
    import datetime as dt
    try:
        day = dt.date.fromisoformat(product.get("lookup_missed_on") or "")
    except ValueError:
        return False
    return (timeutil.now().date() - day).days < MISS_DAYS


def set_bought(batch_id, units, packs=None):
    """Fix how much one purchase was: packs bought (with the product's pack size) or the amount itself."""
    change = {"units": round(units, 4)}
    if packs is not None:
        change["packs"] = packs
    sheets.update_rows("batches", "batch_id", {batch_id: change})


def save_typical(product_id, item_name, match, grams=None, portion=""):
    """Typical nutrition from USDA (per 100 g) for food without a label. A label's own nutrition always wins:
    this never replaces nutrition read from a label or a product page."""
    product = next((p for p in sheets.read_tab("products") if p["product_id"] == product_id), {})
    if product.get("nutrients_json") and not str(product.get("info_source", "")).startswith("USDA"):
        return False
    info = productinfo._from_ai({"name": match["name"], "serving_size": "100 g", "nutrients": match["nutrients"]},
                                f"USDA typical: {match['name']}", "", [])
    cols = productinfo.product_columns(info)
    change = {"nutrients_json": cols["nutrients_json"], "serving_size": "100 g", "info_source": info["source"],
              "info_url": f"https://fdc.nal.usda.gov/food-details/{match['fdc_id']}/nutrients"}
    if grams:
        change["piece_grams"] = grams
        change["details"] = "; ".join(x for x in [product.get("details", ""), f"1 piece ≈ {grams:g} g ({portion})"] if x)
    if not product.get("ingredients"):
        change.update(ingredients=item_name, info_status="confirmed")
    sheets.update_rows("products", "product_id", {product_id: change})
    return True


def fill_typical(purchase_id):
    """After saving a purchase: loose food (fruit, vegetables, raw meat, fish) gets USDA typical nutrition,
    a few at a time. Quietly skips anything it can't match; the item panel can always fix the match."""
    from grocery import usda
    if not usda.api_key():
        return 0
    item_rows = {i["item_id"]: i for i in items()}
    prods = {p["product_id"]: p for p in sheets.read_tab("products")}
    todo = {}
    for b in sheets.read_tab("batches"):
        p, item = prods.get(b.get("product_id")), item_rows.get(b.get("item_id"), {})
        if (b.get("purchase_id") == purchase_id and p and p.get("packaged") == "no" and item.get("kind") == "Food"
                and not p.get("nutrients_json")):
            todo[p["product_id"]] = (item["name"], item.get("unit") == "each")

    def one(pid, name, by_piece):
        found = usda.matches(name, limit=1)
        if not found:
            return False
        grams, portion = usda.piece_grams(found[0]["fdc_id"]) if by_piece else (None, "")
        return save_typical(pid, name, found[0], grams, portion)

    from concurrent.futures import ThreadPoolExecutor
    try:
        from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx
        ctx = get_script_run_ctx()
        start = (lambda: add_script_run_ctx(ctx=ctx)) if ctx else None
    except ImportError:
        start = None
    done = 0
    with ThreadPoolExecutor(max_workers=4, initializer=start) as pool:
        for ok in pool.map(lambda kv: _quietly(one, kv[0], *kv[1]), todo.items()):
            done += bool(ok)
    return done


def _quietly(fn, *args):
    try:
        return fn(*args)
    except Exception:
        return False


NO_NUTRITION = "chosen"


def nutrition_done(product):
    """Nutrition is done when it has values, or you chose "No nutrition" with the USDA option there."""
    return bool(product.get("nutrients_json")) or product.get("nutrition_status") == NO_NUTRITION


def mark_no_nutrition(product_id):
    """You checked: no nutrition label worth tracking (e.g. a spice). Counts as done."""
    # "chosen": decided with typical USDA nutrition on offer. Older "none" marks were made before that
    # existed, so for food they no longer count as done.
    sheets.update_rows("products", "product_id", {product_id: {"nutrition_status": NO_NUTRITION}})


def completeness(product, item, batch):
    """[(what, filled?)] for the profile bar: what's known about this purchase's product and item."""
    item, batch = item or {}, batch or {}
    packaged = bool(product) and product.get("packaged") != "no"
    photo = (product or {}).get("photo_url") if packaged else item.get("photo_url") not in ("", "none", None)
    checks = [("photo", bool(photo)), ("kept in", bool(item.get("storage")))]
    if product:  # which group it belongs to (things you use interchangeably), confirmed by you
        checks.append(("group", product.get("group_status") == "ok"))
    if item.get("perishable") == "yes":
        checks.append(("expiry", bool(batch.get("expiry_date")) or item.get("no_expiry") == "yes"))
    if product and not packaged and item.get("kind", "Food") == "Food":  # loose food: 📚 typical nutrition
        checks.append(("nutrition", nutrition_done(product)))
    if packaged:
        no_label = product.get("info_status") == "none"
        checks += [("brand", bool(product.get("brand")) or product.get("no_brand") == "yes"),
                   ("pack size", bool(_float(product.get("pack_size"))) or product.get("pack_varies") == "yes")]
        if item.get("kind", "Food") == "Food":  # moisturizer, detergent…: no food label to complete
            checks += [
                ("ingredients", bool(product.get("ingredients")) or no_label),
                ("nutrition", nutrition_done(product)),
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
    days = {(x.get("purchased_on"), norm(x.get("store", ""))) for x in live_batches()
            if x.get("product_id") == into_id and x.get("status") == "open"}
    same_day = sum(1 for x in moved if x.get("status") == "open" and (x.get("purchased_on"), norm(x.get("store", ""))) in days)
    return {"from": a, "into": b, "moved": moved, "brings": brings, "nicknames": new_names, "same_day": same_day}


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
    plan["combined"] = combine_same_day(into_id)
    return plan


def combine_same_day(product_id):
    """Open purchases of one product bought the same day at the same store become one purchase (the store
    scanned the same thing twice): amounts, packs and prices are added up. The extra rows are marked deleted
    and anything recorded on them (waste, expiry) moves to the one kept. Returns {absorbed id: kept id}."""
    open_rows = [b for b in live_batches() if b.get("product_id") == product_id and b.get("status") == "open"]
    by_day = {}
    for b in sorted(open_rows, key=lambda b: b.get("created_at", "")):
        by_day.setdefault((b.get("purchased_on", ""), norm(b.get("store", ""))), []).append(b)
    absorbed, change, share = {}, {}, {}
    for rows in by_day.values():
        if len(rows) < 2:
            continue
        keep = rows[0]
        whole = sum(_float(r.get("units")) or 1.0 for r in rows)
        for r in rows:  # each row's part of the combined purchase, to rescale what went bad on it
            share[r["batch_id"]] = (_float(r.get("units")) or 1.0) / whole

        def total(col):
            vals = [_float(r.get(col)) for r in rows]
            return round(sum(v for v in vals if v), 4) if any(vals) else ""
        change[keep["batch_id"]] = {"units": total("units"), "packs": total("packs"), "price_paid": total("price_paid")}
        for r in rows[1:]:
            change[r["batch_id"]] = {"status": "deleted"}
            absorbed[r["batch_id"]] = keep["batch_id"]
    if change:
        sheets.update_rows("batches", "batch_id", change)
        moved = {}
        for e in sheets.read_tab("events"):
            bid = e.get("batch_id")
            if bid not in share:
                continue
            up = {"batch_id": absorbed.get(bid, bid)}
            if e.get("type") == "wasted" and _float(e.get("wasted_fraction")):
                up["wasted_fraction"] = round(_float(e["wasted_fraction"]) * share[bid], 4)  # 1/4 of one of 2 packs = 1/8
            moved[e["event_id"]] = up
        if moved:
            sheets.update_rows("events", "event_id", moved)
    return absorbed


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
    """Keep the picture itself (a small copy) rather than a link that can break later."""
    sheets.update_rows("products", "product_id", {product_id: {"photo_url": productinfo.fetch_picture(photo) or photo}})


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


def _whole_packs(batch):
    """Bought as whole packs (1, 2, 5 crates…), as opposed to an amount said in units (stored as 0.5 of a pack)."""
    packs = _float(batch.get("packs"))
    return bool(packs) and packs >= 1 and abs(packs - round(packs)) < 1e-9


def _to_resize(batches, pack_size, unit, recount=False):
    """[(batch, new units)] for purchases still waiting for this pack size, where it changes something.
    recount=True: also purchases bought as whole packs, because the pack size itself was corrected."""
    if not pack_size:
        return []
    out = []
    for b in batches:
        if _waiting_for_pack_size(b) or (recount and _whole_packs(b)):
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


def convert(amount, from_unit, to_unit):
    """Amount in from_unit expressed in to_unit, or None when they can't be converted (e.g. each -> fl oz)."""
    return productinfo.convert(amount, from_unit, to_unit)


def change_item_unit(item_id, new_unit, product_id):
    """Measure an item in a new unit from now on. Amounts that can be converted (lb <-> oz, gal <-> fl oz <-> ml)
    are converted. Otherwise (each -> fl oz) each purchase is kept as its number of packs, waiting for the
    product's pack size: `product_id` gets it right away, other products show up in Needs info."""
    item = next((i for i in items() if i["item_id"] == item_id), {})
    old_unit = item.get("unit") or "each"
    if new_unit == old_unit:
        return
    sheets.update_rows("items", "item_id", {item_id: {"unit": new_unit}})
    prods = {p["product_id"]: p for p in products() if p.get("item_id") == item_id}
    prod_changes, batch_changes = {}, {}
    for p in prods.values():
        size = _float(p.get("pack_size"))
        new_size = convert(size, p.get("pack_unit") or old_unit, new_unit)
        prod_changes[p["product_id"]] = {"pack_size": round(new_size, 4) if new_size else "", "pack_unit": new_unit if new_size else ""}
    for b in sheets.read_tab("batches"):
        if b.get("item_id") != item_id:
            continue
        units, packs = _float(b.get("units")), _float(b.get("packs"))
        moved = convert(units, b.get("unit") or old_unit, new_unit)
        if moved is not None and b.get("product_id") != product_id:
            batch_changes[b["batch_id"]] = {"units": round(moved, 4), "unit": new_unit}
            continue
        # Not convertible (or the product being fixed now): keep the number of packs; units wait for the pack size.
        size = _float(prods.get(b.get("product_id"), {}).get("pack_size"))
        packs = packs or (units / size if units and size else 1.0)
        batch_changes[b["batch_id"]] = {"packs": round(packs, 6), "units": round(packs, 6), "unit": new_unit}
    sheets.update_rows("products", "product_id", prod_changes)
    sheets.update_rows("batches", "batch_id", batch_changes)


def apply_pack_size(product_id, pack_units, unit, fix_batches=True, count=None, recount=False):
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
    resize = _to_resize(mine, pack_units, unit, recount)
    changes = {b["batch_id"]: {"units": new, "unit": unit or b.get("unit"),
                               **({} if _float(b.get("packs")) is not None else {"packs": 1})}
               for b, new in resize}
    for b in mine:  # amounts that stay as they are still get their pack count (12 each of a 12-pack = 1 pack)
        units = _float(b.get("units"))
        if b["batch_id"] not in changes and _float(b.get("packs")) is None and units and pack_units \
                and (b.get("unit") or unit) == unit:
            changes[b["batch_id"]] = {"packs": round(units / pack_units, 6)}
    sheets.update_rows("batches", "batch_id", changes)
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
