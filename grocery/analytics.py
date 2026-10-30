"""Everything calculated from the raw rows: lifespans, warnings, best place to buy.

Nothing here is stored; change a formula and all history is recalculated.

Per finished batch (status "closed"):
    days         = closed_on - purchased_on   (at least 1)
    eaten share  = 1 - wasted share           (wasted share = sum of its "wasted" events)
    units eaten  = units bought × eaten share
Per item, over its last N finished batches:
    usual days per unit = Σ(eaten share × days) ÷ Σ(units eaten)
Per open batch:
    expected lifespan = units × usual days per unit
"""

import datetime as dt

from grocery import events, sheets, timeutil


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _date(text):
    try:
        return dt.date.fromisoformat(str(text)[:10])
    except ValueError:
        return None


def settings():
    s = sheets.get_settings()
    return {
        "min_batches": int(_num(s["min_batches_before_warning"], 3)),
        "multiplier": _num(s["warning_multiplier"], 1.25),
        "window": int(_num(s["running_average_batches"], 5)),
        "expiry_days": int(_num(s["expiry_warning_days"], 2)),
        "best_min": int(_num(s["best_buy_min_batches"], 2)),
    }


def finished_batches(item_id, batches=None, all_events=None):
    """Finished batches of an item, oldest first, each with days / wasted / eaten worked out."""
    batches = sheets.read_tab("batches") if batches is None else batches
    all_events = events.all_events() if all_events is None else all_events
    out = []
    for b in batches:
        if b.get("item_id") != item_id or b.get("status") != "closed":
            continue
        start, end = _date(b.get("purchased_on")), _date(b.get("closed_on"))
        if not (start and end):
            continue
        wasted = events.wasted_share(b["batch_id"], all_events)
        units = _num(b.get("units"), 1.0) or 1.0
        out.append(dict(b, days=max(1, (end - start).days), wasted=wasted,
                        eaten_share=1 - wasted, units_num=units, units_eaten=units * (1 - wasted),
                        price=_num(b.get("price_paid"), None)))
    return sorted(out, key=lambda b: (b["purchased_on"], b["closed_on"]))


def usual_days_per_unit(finished, window):
    recent = finished[-window:]
    eaten = sum(b["units_eaten"] for b in recent)
    if eaten <= 0:
        return None
    return sum(b["eaten_share"] * b["days"] for b in recent) / eaten


def running_average(finished, window):
    """Usual days per unit after each batch, for the chart's line."""
    return [usual_days_per_unit(finished[:i + 1], window) for i in range(len(finished))]


def pantry():
    """Open batches with status colours: [{batch, item, days_open, expected, level, reason}]."""
    cfg = settings()
    today = timeutil.now().date()
    batches = sheets.read_tab("batches")
    all_events = events.all_events()
    items = {i["item_id"]: i for i in sheets.read_tab("items")}
    usual = {}
    out = []
    for b in batches:
        if b.get("status") != "open":
            continue
        item = items.get(b.get("item_id"), {})
        bought = _date(b.get("purchased_on"))
        days_open = (today - bought).days if bought else 0
        if b["item_id"] not in usual:
            done = finished_batches(b["item_id"], batches, all_events)
            usual[b["item_id"]] = (usual_days_per_unit(done, cfg["window"]), len(done))
        per_unit, n_done = usual[b["item_id"]]
        expected = per_unit * (_num(b.get("units"), 1.0) or 1.0) if per_unit else None

        level, reason = "ok", ""
        expiry = _date(b.get("expiry_date"))
        if expiry and (expiry - today).days <= cfg["expiry_days"]:
            left = (expiry - today).days
            level, reason = "red", ("expired" if left < 0 else "expires today" if left == 0 else f"expires in {left}d")
        elif (item.get("perishable") == "yes" and expected and n_done >= cfg["min_batches"]
              and days_open > cfg["multiplier"] * expected):
            level, reason = "red", f"usually gone in {expected:.0f}d"
        elif expected and days_open > expected and item.get("perishable") == "yes":
            level, reason = "orange", f"usually {expected:.0f}d"
        out.append({"batch": b, "item": item, "days_open": days_open, "expected": expected,
                    "level": level, "reason": reason})
    return out


def warnings():
    return [p for p in pantry() if p["level"] == "red"]


def item_summary(item_id):
    """Everything Insights shows for one item."""
    cfg = settings()
    done = finished_batches(item_id)
    per_unit = usual_days_per_unit(done, cfg["window"])
    return {"finished": done, "per_unit": per_unit, "running": running_average(done, cfg["window"]),
            "best": best_place(done, cfg["best_min"]), "cfg": cfg}


def best_place(finished, min_batches):
    """Compare store + product options by price per unit actually eaten.

    Returns {"options": [...], "winner": option or None, "ready": bool}.
    """
    groups = {}
    for b in finished:
        if b["price"] is None:
            continue
        key = (b.get("store", ""), b.get("product", "") or b.get("item", ""))
        groups.setdefault(key, []).append(b)
    options = []
    for (store, product), rows in groups.items():
        bought = sum(r["units_num"] for r in rows)
        eaten = sum(r["units_eaten"] for r in rows)
        paid = sum(r["price"] for r in rows)
        options.append({
            "store": store, "product": product, "batches": len(rows),
            "shelf_per_unit": paid / bought if bought else None,
            "wasted": 1 - eaten / bought if bought else 0,
            "per_eaten_unit": paid / eaten if eaten else None,
            "enough": len(rows) >= min_batches,
        })
    options.sort(key=lambda o: (o["per_eaten_unit"] is None, o["per_eaten_unit"] or 0))
    qualified = [o for o in options if o["enough"] and o["per_eaten_unit"] is not None]
    ready = len(qualified) >= 2
    return {"options": options, "winner": qualified[0] if ready else None, "ready": ready}


def data_level(item_id, batches, all_events, cfg):
    """green = enough finished batches for warnings, orange = some, red = none."""
    n = len(finished_batches(item_id, batches, all_events))
    return ("green" if n >= cfg["min_batches"] else "orange" if n else "red"), n
