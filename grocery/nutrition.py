"""How much nutrition is in a purchase: the label gives it per serving (or per 100 g), so we need how many
servings the amount bought holds. Worked out, best source first:
  1. servings per pack (from the label) x packs;
  2. the weight in grams (or volume in ml) of what was bought / the serving's weight, where the weight comes from
     the amount itself (lb, oz, gal…), the weight you gave for one pack, or one piece's weight x pieces;
otherwise unknown ("weight?" in Needs info), and it's left out of the totals rather than guessed.
Weight and volume are never mixed without a density (g/ml) you entered (decided): a liquid without one shows
"density?"; a powder whose serving is in spoons while its pack is a weight needs the label's servings per
container. The label's servings per container is about ONE manufacturer unit, multiplied by the units in a pack.
"""

import json
import re

GRAMS = {"g": 1.0, "kg": 1000.0, "oz": 28.3495, "lb": 453.592,
         "ml": 1.0, "l": 1000.0, "fl oz": 29.5735, "qt": 946.353, "gal": 3785.41}
VOLUME = {"ml", "l", "fl oz", "qt", "gal"}  # GRAMS gives these in ml; the rest in grams
_AMOUNT = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|g|grams?|ml|mL|l|L|fl\.?\s*oz|oz|lbs?)\b", re.I)
_MULTI = re.compile(r"(\d+)\s*(?:x|×)\s*(\d+(?:\.\d+)?)\s*(kg|g|grams?|ml|mL|l|L|fl\.?\s*oz|oz|lbs?)\b", re.I)
SPOONS = {"tsp": 4.93, "teaspoon": 4.93, "teaspoons": 4.93, "tbsp": 14.79, "tablespoon": 14.79,
          "tablespoons": 14.79, "cup": 236.6, "cups": 236.6}
_SPOON = re.compile(r"(\d+(?:\.\d+)?|\d+\s*/\s*\d+)\s*(tsp|teaspoons?|tbsp|tablespoons?|cups?)\b", re.I)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _unit(u):
    u = re.sub(r"\s+", " ", str(u).lower().replace(".", "")).strip()
    return {"gram": "g", "grams": "g", "lbs": "lb", "floz": "fl oz"}.get(u, u)


def _amount(value, unit):
    """(amount in g or ml, "weight"/"volume") for a number and unit, or None."""
    u = _unit(unit)
    if u not in GRAMS:
        return None
    return value * GRAMS[u], ("volume" if u in VOLUME else "weight")


def _number(text):
    text = text.replace(" ", "")
    if "/" in text:
        a, b = text.split("/", 1)
        return _f(a) / _f(b) if _f(b) else 0.0
    return _f(text)


def serving_amount(product):
    """One serving as (amount, "weight"/"volume"): grams or ml printed in the serving text ("3/4 cup (170g)",
    "8 fl oz (240 mL)", "100 g"), else a spoon or cup measure as ml ("1/4 tsp" = 1.23 ml). None if not given."""
    text = str((product or {}).get("serving_size") or "")
    found = [(m.start(), _amount(_f(m.group(1)), m.group(2))) for m in _AMOUNT.finditer(text)]
    found = [(pos, a) for pos, a in found if a and a[0] > 0]
    if found:
        inside = [a for pos, a in found if "(" in text[:pos] and ")" in text[pos:]]
        return (inside or [found[0][1]])[0]
    m = _SPOON.search(text)
    if m:
        return _number(m.group(1)) * SPOONS[m.group(2).lower()], "volume"
    return None


def serving_grams(text):
    a = serving_amount({"serving_size": text})
    return a[0] if a else None


def per_serving(product):
    """{nutrient: amount} per serving (or per 100 g for USDA typical values)."""
    try:
        per = json.loads((product or {}).get("nutrients_json") or "{}").get("per_serving", {})
    except ValueError:
        return {}
    return {k: _f(v.get("amount")) for k, v in per.items() if isinstance(v, dict) and v.get("amount") is not None}


def name_amount(product):
    """One store pack's weight/volume printed in its name or pack text: "Shan Biryani Masala 50g" -> 50 g,
    "A2 Yogurt, 2 x 32 oz" -> 64 oz (2 units of 32). (amount, kind) or None."""
    for text in ((product or {}).get("pack_text"), (product or {}).get("name")):
        text = str(text or "")
        m = _MULTI.search(text)
        if m:
            a = _amount(_f(m.group(2)), m.group(3))
            if a:
                return int(m.group(1)) * a[0], a[1]
        found = [_amount(_f(m.group(1)), m.group(2)) for m in _AMOUNT.finditer(text)]
        found = [a for a in found if a and a[0] > 0]
        if found:
            return found[-1]
    return None


def name_grams(product):
    a = name_amount(product)
    return a[0] if a else None


def name_units(product):
    """Units in one store pack per its name ("2 x 32 oz" -> 2), else None."""
    m = _MULTI.search(str((product or {}).get("name") or ""))
    return int(m.group(1)) if m else None


def amount_of(units, unit, product):
    """`units` of this product as (amount in g or ml, kind), or None."""
    product = product or {}
    units, unit = _f(units), _unit(unit or "each")
    if not units:
        return None
    if unit in GRAMS:
        return _amount(units, unit)
    if unit == "each" and _f(product.get("piece_grams")):  # one piece's weight, given or from USDA, wins
        return units * _f(product["piece_grams"]), "weight"
    size = _f(product.get("pack_size")) or (1.0 if unit == "each" else 0.0)
    if _f(product.get("pack_grams")) and size and _unit(product.get("pack_unit") or unit) == unit:
        return _f(product["pack_grams"]) * units / size, "weight"
    if unit == "each" and size == 1:
        a = name_amount(product)
        if a:
            return a[0] * units, a[1]
    if unit == "each" and _f(product.get("piece_grams")):
        return units * _f(product["piece_grams"]), "weight"
    return None


def grams_of(units, unit, product):
    a = amount_of(units, unit, product)
    return a[0] if a else None


def units_per_pack(product):
    """How many manufacturer units one store pack holds (a 2-pack of tubs = 2): the label's "servings per
    container" is about ONE unit, so it's multiplied by this."""
    return int(_f((product or {}).get("pack_count")) or name_units(product) or 1)


def _match(serving, amount, density):
    """Servings in `amount` given one serving, converting weight <-> volume only with a known density (g/ml)."""
    (s_amt, s_kind), (a_amt, a_kind) = serving, amount
    if s_kind != a_kind:
        if not density:
            return None
        a_amt = a_amt * density if a_kind == "volume" else a_amt / density
    return a_amt / s_amt if s_amt else None


def servings(units, unit, product):
    """How many servings `units` of this product holds, and how it was worked out; (None, "") if unknown."""
    product = product or {}
    spp, size = _f(product.get("servings_per_pack")), _f(product.get("pack_size"))
    if spp and size and _unit(product.get("pack_unit") or unit) == _unit(unit) and _f(units):
        n = units_per_pack(product)
        return spp * n * _f(units) / size, ("servings per container on the label"
                                            + (f" × {n} units in a pack" if n > 1 else ""))
    serving, amount = serving_amount(product), amount_of(units, unit, product)
    if serving and amount:
        n = _match(serving, amount, _f(product.get("density")))
        if n is not None:
            return n, (f"{amount[0]:,.0f} {'ml' if amount[1] == 'volume' else 'g'} ÷ {serving[0]:g} "
                       f"{'ml' if serving[1] == 'volume' else 'g'} per serving"
                       + (f" (density {_f(product['density']):g} g/ml)" if serving[1] != amount[1] else ""))
    return None, ""


def pack_servings(product, item):
    """Servings in one pack (or in one piece / one unit when there's no pack size)."""
    product = product or {}
    size = _f(product.get("pack_size")) or 1.0
    unit = product.get("pack_unit") or (item or {}).get("unit") or "each"
    return servings(size, unit, product)


def what_is_missing(product, item):
    """Why the servings can't be worked out: (pack amount, serving amount, problem) where problem is
    "weight" (pack amount unknown), "serving" (serving amount unknown), "density" (a liquid: weight vs volume),
    "mismatch" (serving in spoons/volume but the pack is a weight: a powder) or "" when nothing's missing."""
    product = product or {}
    size = _f(product.get("pack_size")) or 1.0
    unit = product.get("pack_unit") or (item or {}).get("unit") or "each"
    amount, serving = amount_of(size, unit, product), serving_amount(product)
    if pack_servings(product, item)[0] is not None:
        return amount, serving, ""
    if not amount:
        return amount, serving, "weight"
    if not serving:
        return amount, serving, "serving"
    if amount[1] == "volume":
        return amount, serving, "density"  # a liquid: its density turns grams into ml
    return amount, serving, "mismatch"  # a powder measured in spoons: needs servings per container


def needs_weight(product, item):
    """The problem stopping nutrition totals ("weight", "serving", "density", "mismatch"), or "" if none."""
    if not per_serving(product):
        return ""
    return what_is_missing(product, item)[2]


def pack_amount_servings(packs, product):
    """Servings in `packs` whole packs of a product whose pack size isn't set (the purchase was saved as packs
    waiting for a size): from the label's servings per container × units per pack, else the pack's weight from
    what you entered or its name ("Heinz Ketchup 32 oz") ÷ one serving. None when there's no way to tell."""
    product = product or {}
    spp = _f(product.get("servings_per_pack"))
    if spp:
        return spp * units_per_pack(product) * _f(packs)
    amount = (_f(product.get("pack_grams")), "weight") if _f(product.get("pack_grams")) else name_amount(product)
    serving = serving_amount(product)
    if amount and serving:
        n = _match(serving, amount, _f(product.get("density")))
        return n * _f(packs) if n is not None else None
    return None


def nutrients_in(units, unit, product, waiting=False):
    """{nutrient: total amount} in `units` of this product, or None if the amount isn't known.
    waiting=True: `units` are whole packs whose size isn't known yet."""
    if waiting:
        n = pack_amount_servings(units, product)
        return None if n is None else {k: v * n for k, v in per_serving(product).items()}
    n, _ = servings(units, unit, product)
    if n is None:
        return None
    return {k: v * n for k, v in per_serving(product).items()}
