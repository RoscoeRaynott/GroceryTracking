"""How much nutrition is in a purchase: the label gives it per serving (or per 100 g), so we need how many
servings the amount bought holds. Worked out, best source first:
  1. servings per pack (from the label) x packs;
  2. the weight in grams (or volume in ml) of what was bought / the serving's weight, where the weight comes from
     the amount itself (lb, oz, gal…), the weight you gave for one pack, or one piece's weight x pieces;
otherwise unknown ("weight?" in Needs info), and it's left out of the totals rather than guessed.
Assumption (decided): 1 ml counts as 1 g when one side is a weight and the other a volume.
"""

import json
import re

GRAMS = {"g": 1.0, "kg": 1000.0, "oz": 28.3495, "lb": 453.592,
         "ml": 1.0, "l": 1000.0, "fl oz": 29.5735, "qt": 946.353, "gal": 3785.41}
_AMOUNT = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|g|grams?|ml|mL|l|L|fl\.?\s*oz|oz|lbs?)\b", re.I)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _unit(u):
    u = re.sub(r"\s+", " ", str(u).lower().replace(".", "")).strip()
    return {"gram": "g", "grams": "g", "lbs": "lb", "floz": "fl oz"}.get(u, u)


def serving_grams(text):
    """Grams (or ml) in one serving, from text like "3/4 cup (170g)", "100 g", "8 fl oz (240 mL)"."""
    text = str(text or "")
    found = [(m.start(), _f(m.group(1)) * GRAMS.get(_unit(m.group(2)), 0)) for m in _AMOUNT.finditer(text)]
    found = [f for f in found if f[1] > 0]
    if not found:
        return None
    inside = [g for pos, g in found if "(" in text[:pos] and ")" in text[pos:]]
    return (inside or [found[0][1]])[0]


def per_serving(product):
    """{nutrient: amount} per serving (or per 100 g for USDA typical values)."""
    try:
        per = json.loads((product or {}).get("nutrients_json") or "{}").get("per_serving", {})
    except ValueError:
        return {}
    return {k: _f(v.get("amount")) for k, v in per.items() if isinstance(v, dict) and v.get("amount") is not None}


SPOONS = {"tsp": 4.93, "teaspoon": 4.93, "teaspoons": 4.93, "tbsp": 14.79, "tablespoon": 14.79,
          "tablespoons": 14.79, "cup": 236.6, "cups": 236.6}
_SPOON = re.compile(r"(\d+(?:\.\d+)?|\d+\s*/\s*\d+)\s*(tsp|teaspoons?|tbsp|tablespoons?|cups?)\b", re.I)
VOLUME = {"ml", "l", "fl oz", "qt", "gal"}


def _number(text):
    text = text.replace(" ", "")
    if "/" in text:
        a, b = text.split("/", 1)
        return _f(a) / _f(b) if _f(b) else 0.0
    return _f(text)


def name_grams(product):
    """One pack's weight/volume printed in its name or pack text ("Shan Biryani Masala 50g", "Sauce, 8 fl oz")."""
    for text in ((product or {}).get("pack_text"), (product or {}).get("name")):
        found = [_f(m.group(1)) * GRAMS.get(_unit(m.group(2)), 0) for m in _AMOUNT.finditer(str(text or ""))]
        found = [g for g in found if g > 0]
        if found:
            return found[-1]
    return None


def grams_of(units, unit, product):
    """The weight in grams (or ml) of `units` of this product, or None."""
    product = product or {}
    units, unit = _f(units), _unit(unit or "each")
    if not units:
        return None
    if unit in GRAMS:
        return units * GRAMS[unit]
    size = _f(product.get("pack_size")) or (1.0 if unit == "each" else 0.0)
    pack_grams = _f(product.get("pack_grams")) or (name_grams(product) if unit == "each" and size == 1 else None)
    if pack_grams and size and _unit(product.get("pack_unit") or unit) == unit:
        return pack_grams * units / size
    piece = _f(product.get("piece_grams"))
    if unit == "each" and piece:
        return units * piece
    return None


def serving_amount(product, unit):
    """Grams (or ml) in one serving: printed in grams/ml, or for a liquid a spoon or cup measure (1 tbsp = 15 ml).
    Spoons of a powder are not converted (a teaspoon of spice weighs anything from 1 to 3 g)."""
    text = (product or {}).get("serving_size")
    sg = serving_grams(text)
    if sg:
        return sg
    liquid = _unit(unit) in VOLUME or _unit((product or {}).get("pack_unit") or "") in VOLUME
    m = _SPOON.search(str(text or ""))
    if liquid and m:
        return _number(m.group(1)) * SPOONS[m.group(2).lower()]
    return None


def servings(units, unit, product):
    """How many servings `units` of this product holds, and how it was worked out; (None, "") if unknown."""
    product = product or {}
    spp, size = _f(product.get("servings_per_pack")), _f(product.get("pack_size"))
    if spp and size and _unit(product.get("pack_unit") or unit) == _unit(unit) and _f(units):
        return spp * _f(units) / size, "servings per pack on the label"
    sg = serving_amount(product, unit)
    grams = grams_of(units, unit, product)
    if sg and grams:
        return grams / sg, f"{grams:,.0f} g ÷ {sg:g} g per serving"
    return None, ""


def pack_servings(product, item):
    """Servings in one pack (or in one piece / one unit when there's no pack size)."""
    product = product or {}
    size = _f(product.get("pack_size")) or 1.0
    unit = product.get("pack_unit") or (item or {}).get("unit") or "each"
    return servings(size, unit, product)


def what_is_missing(product, item):
    """For the "weight?" question: (one pack's grams or None, one serving's grams or None)."""
    product = product or {}
    size = _f(product.get("pack_size")) or 1.0
    unit = product.get("pack_unit") or (item or {}).get("unit") or "each"
    return grams_of(size, unit, product), serving_amount(product, unit)


def needs_weight(product, item):
    """True when it has nutrition values but we can't tell how much of it a pack holds."""
    if not per_serving(product):
        return False
    return pack_servings(product, item)[0] is None


def pack_amount_servings(packs, product):
    """Servings in `packs` whole packs of a product whose pack size isn't set (the purchase was saved as packs
    waiting for a size): from the label's servings per pack, else the pack's weight from what you entered or its
    name ("Heinz Ketchup 32 oz") ÷ one serving. None when there's no way to tell."""
    product = product or {}
    spp = _f(product.get("servings_per_pack"))
    if spp:
        return spp * _f(packs)
    pg = _f(product.get("pack_grams")) or name_grams(product)
    sg = serving_amount(product, product.get("pack_unit") or "")
    if not sg and pg:  # a liquid's spoon measure needs to know it's a liquid: the name's unit tells
        m = _AMOUNT.search(str(product.get("name") or ""))
        sg = serving_amount(product, _unit(m.group(2))) if m else None
    return _f(packs) * pg / sg if pg and sg else None


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
