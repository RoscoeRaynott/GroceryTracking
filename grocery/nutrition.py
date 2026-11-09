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


def grams_of(units, unit, product):
    """The weight in grams (or ml) of `units` of this product, or None."""
    product = product or {}
    units, unit = _f(units), _unit(unit or "each")
    if not units:
        return None
    if unit in GRAMS:
        return units * GRAMS[unit]
    size = _f(product.get("pack_size"))
    pack_grams = _f(product.get("pack_grams"))
    if pack_grams and size and _unit(product.get("pack_unit") or unit) == unit:
        return pack_grams * units / size
    piece = _f(product.get("piece_grams"))
    if unit == "each" and piece:
        return units * piece
    return None


def servings(units, unit, product):
    """How many servings `units` of this product holds, and how it was worked out; (None, "") if unknown."""
    product = product or {}
    spp, size = _f(product.get("servings_per_pack")), _f(product.get("pack_size"))
    if spp and size and _unit(product.get("pack_unit") or unit) == _unit(unit) and _f(units):
        return spp * _f(units) / size, "servings per pack on the label"
    sg = serving_grams(product.get("serving_size"))
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


def needs_weight(product, item):
    """True when it has nutrition values but we can't tell how much of it a pack holds."""
    if not per_serving(product):
        return False
    return pack_servings(product, item)[0] is None


def nutrients_in(units, unit, product):
    """{nutrient: total amount} in `units` of this product, or None if the weight isn't known."""
    n, _ = servings(units, unit, product)
    if n is None:
        return None
    return {k: v * n for k, v in per_serving(product).items()}
