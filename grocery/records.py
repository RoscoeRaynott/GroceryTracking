"""Reading and writing the app's records in the Sheet."""

import json
import re
import uuid

from grocery import productinfo, sheets, timeutil

CHAINS = {
    "costco", "walmart", "safeway", "kroger", "h-e-b", "heb", "target", "trader joe's",
    "whole foods", "sam's club", "aldi", "randalls", "fiesta", "sprouts", "instacart",
}


def new_id(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def norm(text):
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def items():
    return sheets.read_tab("items")


def stores():
    return sheets.read_tab("stores")


def open_batches_by_item():
    out = {}
    for b in sheets.read_tab("batches"):
        if b.get("status") == "open":
            out.setdefault(b.get("item_id"), []).append(b)
    return out


def find_duplicate_purchase(store, date, total):
    """An earlier purchase with the same store, date and total, if any."""
    if not (store and date and total not in (None, "")):
        return None
    for p in sheets.read_tab("purchases"):
        try:
            same_total = abs(float(p.get("total_paid") or "nan") - float(total)) < 0.005
        except ValueError:
            same_total = False
        if norm(p.get("store")) == norm(store) and str(p.get("purchased_at", "")).startswith(str(date)) and same_total:
            return p
    return None


def save_purchase(review, finished, draft_id, readings):
    """Write one reviewed receipt to the Sheet.

    review   = {"store", "date", "time", "source", "total_paid", "items": [card dicts]}
    finished = {item_id: "YYYY-MM-DD"} open batches the user said are finished
    readings = [(task, model, json-able reading)] kept in ai_readings
    """
    now = timeutil.now_str()
    store_name = canonical_store(review["store"])

    store = next((s for s in stores() if norm(s["name"]) == norm(store_name)), None)
    if store is None:
        store = {"store_id": new_id("S"), "name": store_name,
                 "type": "chain" if norm(store_name) in CHAINS else "local", "created_at": now}
        sheets.append_rows("stores", [store])

    known = {norm(i["name"]): i for i in items()}
    new_items = []
    for card in review["items"]:
        if norm(card["item"]) not in known:
            row = {
                "item_id": new_id("I"), "name": card["item"].strip(), "kind": card["kind"],
                "perishable": card["perishable"] if card["kind"] != "One-time" else False,
                "unit": card["unit"], "storage": card["storage"], "emoji": card.get("emoji", ""),
                "tracked": card["kind"] != "One-time", "created_at": now,
            }
            known[norm(row["name"])] = {k: sheets._cell(v) for k, v in row.items()}
            new_items.append(row)
    sheets.append_rows("items", new_items)

    existing_products = sheets.read_tab("products")
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
            row = {"product_id": new_id("P"), "item_id": item["item_id"], "item": item["name"],
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

    purchase_id = new_id("R")
    purchased_at = f"{review['date']} {review['time']}".strip()
    sheets.append_rows("purchases", [{
        "purchase_id": purchase_id, "store_id": store["store_id"], "store": store["name"],
        "purchased_at": purchased_at, "source": review["source"], "items": len(review["items"]),
        "total_paid": review.get("total_paid", ""), "created_at": now,
    }])

    batches = []
    for card in review["items"]:
        item = known[norm(card["item"])]
        product = products.get((item["item_id"], norm(card["product"])), {})
        batches.append({
            "batch_id": new_id("B"), "purchase_id": purchase_id, "item_id": item["item_id"],
            "item": item["name"], "product_id": product.get("product_id", ""),
            "product": product.get("name", ""), "store": store["name"], "purchased_on": review["date"],
            "units": card["units"], "unit": card["unit"], "price_paid": card["price"],
            "status": "open" if card["kind"] != "One-time" else "not tracked", "created_at": now,
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

    sheets.append_rows("ai_readings", [
        {"reading_id": new_id("A"), "purchase_id": purchase_id, "store": store["name"], "at": now,
         "task": task, "model": model, "reading": json.dumps(data, ensure_ascii=False)}
        for task, model, data in readings
    ])
    if draft_id:
        sheets.delete_rows_where("drafts", draft_id=draft_id)
    return purchase_id, len(batches)


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
    """Packaged foods with no ingredients yet, newest first, with their item's emoji."""
    food = {i["item_id"]: i for i in items() if i.get("kind") == "Food"}
    out = []
    for p in sheets.read_tab("products"):
        if p["item_id"] not in food or p.get("packaged") == "no":
            continue
        if p.get("info_status") in ("confirmed", "none") or p.get("ingredients"):
            continue
        out.append(dict(p, emoji=food[p["item_id"]].get("emoji", "")))
    return sorted(out, key=lambda p: p.get("created_at", ""), reverse=True)


def save_product_info(product_id, info, barcode=""):
    changes = productinfo.product_columns(info)
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
    rows = sheets.read_tab("purchases")
    return rows[-1]["store"] if rows else ""


def purchase_history(limit=60):
    """Most recent batch per (item, store, product), newest first, for voice quick-add."""
    seen, out = set(), []
    rows = [b for b in sheets.read_tab("batches") if b.get("status") != "deleted"]
    for b in sorted(rows, key=lambda b: b.get("purchased_on", ""), reverse=True):
        key = (b.get("item_id"), norm(b.get("store")), norm(b.get("product")))
        if key in seen:
            continue
        seen.add(key)
        out.append(b)
        if len(out) >= limit:
            break
    return out


def product_search_text(p):
    """Everything known about a product, for an exact web search."""
    latest = max((b for b in sheets.read_tab("batches") if b.get("product_id") == p["product_id"]),
                 key=lambda b: b.get("purchased_on", ""), default={})
    parts = [p.get("brand", ""), p["name"]]
    if latest:
        parts.append(f"{latest.get('units', '')} {latest.get('unit', '')} pack bought at {latest.get('store', '')}")
    if p.get("details"):
        parts.append(f"({p['details']})")
    return " ".join(x for x in parts if x and x.strip())


def save_product_match(product_id, info):
    """The web found the exact product but no label info: remember the match, stop asking."""
    product = next((p for p in sheets.read_tab("products") if p["product_id"] == product_id), {})
    details = "; ".join(x for x in [product.get("details", ""), f"Matched online: {info.get('name', '')}"] if x)
    sheets.update_rows("products", "product_id", {product_id: {
        "info_status": "none", "info_source": "Web search", "info_url": info.get("url", ""), "details": details,
    }})


def canonical_store(name):
    """Costco and Costco Business Center sell the same products: one store, "Costco"."""
    name = re.sub(r"\s+", " ", str(name or "")).strip()
    return "Costco" if "costco" in name.lower() else name


def recent_purchases(limit=20):
    """Newest purchases with their batches: [(purchase row, [batch rows])]."""
    batches = sheets.read_tab("batches")
    rows = sorted(sheets.read_tab("purchases"), key=lambda p: p.get("created_at", ""), reverse=True)[:limit]
    return [(p, [b for b in batches if b.get("purchase_id") == p["purchase_id"]]) for p in rows]


def delete_purchase(purchase_id):
    """Erase a purchase completely (for test entries): its batches, their events, its AI readings,
    and any item, product or store that nothing else uses any more."""
    batch_ids = {b["batch_id"] for b in sheets.read_tab("batches") if b.get("purchase_id") == purchase_id}
    sheets.remove_rows("events", lambda r: r.get("batch_id") in batch_ids)
    sheets.remove_rows("batches", lambda r: r.get("purchase_id") == purchase_id)
    sheets.remove_rows("purchases", lambda r: r.get("purchase_id") == purchase_id)
    sheets.remove_rows("ai_readings", lambda r: r.get("purchase_id") == purchase_id)

    batches = sheets.read_tab("batches")
    used_items = {b.get("item_id") for b in batches}
    used_products = {b.get("product_id") for b in batches}
    used_stores = {p.get("store_id") for p in sheets.read_tab("purchases")}
    sheets.remove_rows("products", lambda r: r.get("product_id") not in used_products)
    sheets.remove_rows("items", lambda r: r.get("item_id") not in used_items)
    sheets.remove_rows("stores", lambda r: r.get("store_id") not in used_stores)
