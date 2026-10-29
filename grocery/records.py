"""Reading and writing the app's records in the Sheet."""

import json
import re
import uuid

from grocery import sheets, timeutil

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
    store_name = review["store"].strip()

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

    products = {(p["item_id"], norm(p["name"])): p for p in sheets.read_tab("products")}
    new_products = []
    for card in review["items"]:
        item = known[norm(card["item"])]
        key = (item["item_id"], norm(card["product"]))
        if card["product"].strip() and key not in products:
            row = {"product_id": new_id("P"), "item_id": item["item_id"], "item": item["name"],
                   "brand": card.get("brand", ""), "name": card["product"].strip(),
                   "source": "receipt", "created_at": now}
            products[key] = row
            new_products.append(row)
    sheets.append_rows("products", new_products)

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
        open_now = open_batches_by_item()
        closes, events = {}, []
        for item_id, day in finished.items():
            for b in open_now.get(item_id, []):
                if b["batch_id"] in {x["batch_id"] for x in batches}:
                    continue
                closes[b["batch_id"]] = {"status": "closed", "closed_on": day}
                events.append({"event_id": new_id("E"), "batch_id": b["batch_id"], "item": b["item"],
                               "type": "finished", "wasted_fraction": 0, "happened_on": day,
                               "recorded_at": now, "source": "receipt review",
                               "said": f"Finished when restocking at {store['name']}", "undone": False})
        sheets.update_rows("batches", "batch_id", closes)
        sheets.append_rows("events", events)

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
