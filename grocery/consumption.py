"""What you ate, worked out from finished purchases (or finished packs of one), for Insights.

Each finished piece covers the days from when it was started (pack 2 onwards: when the pack before it was
finished) to when it was finished; what was eaten of it (minus what went bad) is spread evenly over those
days. Only finished things count (decided): an open one isn't known yet. Nothing here is stored.
"""

import datetime as dt

from grocery import analytics, events, nutrition, productinfo, records, sheets, timeutil

LIMIT = ("sodium", "saturated_fat", "added_sugars", "cholesterol", "trans_fat")  # the less the better
GET = ("potassium", "dietary_fiber", "protein", "calcium", "iron", "vitamin_d")  # enough is good


def eaten():
    """[{item_id, group, product_id, product, kind, start, end, days, eaten_units, unit, nutrients, counted,
    has_nutrition, price, wasted, wasted_cost, pure_alcohol_floz}] one per finished purchase or pack."""
    items = {i["item_id"]: i for i in records.items()}
    products = {p["product_id"]: p for p in records.products()}
    batches = sheets.read_tab("batches")
    evs = events.all_events()
    out = []
    for item_id, item in items.items():
        for c in analytics.finished_batches(item_id, batches, evs):
            end = analytics._date(c.get("closed_on"))
            if not end:
                continue
            days = int(c.get("eat_days") or c.get("days") or 1)
            product = products.get(c.get("product_id"))
            unit = c.get("unit") or item.get("unit") or "each"
            food = item.get("kind", "Food") == "Food"
            has = bool(food and product and nutrition.per_serving(product))
            # saved as N packs while the pack size wasn't known (units = packs): the amount isn't a real "1 oz"
            waiting = bool(product and not records._float(product.get("pack_size")) and records._waiting_for_pack_size(c)
                           and not (unit == "each" and records._float(product.get("piece_grams"))))
            totals = nutrition.nutrients_in(c["units_eaten"], unit, product, waiting) if has else None
            alcohol = 0.0
            abv = records._float((product or {}).get("abv"))
            if food and abv and records.is_alcohol(product, item):
                floz = productinfo.convert(c["units_eaten"], unit, "fl oz")
                alcohol = floz * abv / 100 if floz else 0.0
            price = c.get("price")
            out.append({
                "item_id": item_id, "group": item.get("name", ""), "product_id": c.get("product_id"),
                "product": records.short_name(product, item) if product else item.get("name", ""),
                "kind": item.get("kind", "Food"), "start": end - dt.timedelta(days=days), "end": end, "days": days,
                "eaten_units": c["units_eaten"], "unit": unit, "nutrients": totals or {}, "counted": totals is not None,
                "has_nutrition": has, "waiting": waiting, "price": price, "wasted": c["wasted"],
                "wasted_cost": (price or 0) * c["wasted"], "pure_alcohol_floz": alcohol,
                "ingredients": (product or {}).get("ingredients", ""), "ingredients_norm": (product or {}).get("ingredients_norm", ""),
            })
    return out


def share_in(rec, p0, p1):
    """The part of a finished piece eaten between p0 and p1 (dates, inclusive), from its even spread."""
    first, last = rec["start"], rec["end"] - dt.timedelta(days=1)  # its `days` days of eating
    lo, hi = max(first, p0), min(last, p1)
    return max(0, (hi - lo).days + 1) / rec["days"] if hi >= lo else 0.0


def totals(recs, p0, p1, key="nutrients"):
    out = {}
    for r in recs:
        s = share_in(r, p0, p1)
        if s:
            for k, v in r[key].items():
                out[k] = out.get(k, 0.0) + v * s
    return out


def spent(p0, p1):
    """Money spent on purchases bought between p0 and p1 (by purchase date)."""
    total = 0.0
    for b in records.live_batches():
        day = analytics._date(b.get("purchased_on"))
        if day and p0 <= day <= p1:
            total += records._float(b.get("price_paid"))
    return total


def period(name):
    today = timeutil.now().date()
    days = {"7 days": 7, "30 days": 30, "3 months": 91, "Year": 365}[name]
    return today - dt.timedelta(days=days - 1), today, days
