"""Finding a food product's ingredients and nutrition facts.

Sources, in order of preference (the label photo always wins):
  1. Open Food Facts by barcode
  2. Open Food Facts by name, only when the brand matches exactly
  3. Gemini + Google Search for that exact brand and product (with sources)
  4. A photo of the label read by Gemini

Every result has the same shape (see _info) so the app can show and save it
the same way. Results from 2 and 3 must be confirmed by the user.
"""

import io
import json
import re
import urllib.parse
import urllib.request

from grocery import gemini

try:
    import zxingcpp
    from PIL import Image
    BARCODES_AVAILABLE = True
except ImportError:
    BARCODES_AVAILABLE = False

USER_AGENT = "GroceryTracking/1.0 (personal grocery tracker)"
OFF_FIELDS = "code,product_name,brands,quantity,serving_size,serving_quantity,ingredients_text,nutriments,image_front_url"

# FDA Daily Values used on US Nutrition Facts labels (21 CFR 101.9).
DAILY_VALUES = {
    "total_fat": 78, "saturated_fat": 20, "cholesterol": 300, "sodium": 2300,
    "total_carbohydrate": 275, "dietary_fiber": 28, "added_sugars": 50, "protein": 50,
    "vitamin_d": 20, "calcium": 1300, "iron": 18, "potassium": 4700,
}
NUTRIENT_UNITS = {
    "calories": "kcal", "total_fat": "g", "saturated_fat": "g", "trans_fat": "g",
    "cholesterol": "mg", "sodium": "mg", "total_carbohydrate": "g", "dietary_fiber": "g",
    "total_sugars": "g", "added_sugars": "g", "protein": "g", "vitamin_d": "mcg",
    "calcium": "mg", "iron": "mg", "potassium": "mg",
}
# Open Food Facts key -> (our key, multiply to reach our unit). OFF stores grams.
OFF_NUTRIENTS = {
    "energy-kcal": ("calories", 1), "fat": ("total_fat", 1), "saturated-fat": ("saturated_fat", 1),
    "trans-fat": ("trans_fat", 1), "cholesterol": ("cholesterol", 1000), "sodium": ("sodium", 1000),
    "carbohydrates": ("total_carbohydrate", 1), "fiber": ("dietary_fiber", 1),
    "sugars": ("total_sugars", 1), "added-sugars": ("added_sugars", 1), "proteins": ("protein", 1),
    "vitamin-d": ("vitamin_d", 1_000_000), "calcium": ("calcium", 1000), "iron": ("iron", 1000),
    "potassium": ("potassium", 1000),
}

NUTRIENT_LIST = ", ".join(f"{k} ({u})" for k, u in NUTRIENT_UNITS.items())
INFO_SHAPE = f"""{{
  "found": true or false,
  "name": product name, "brand": brand,
  "serving_size": serving size as printed (e.g. "2 links (68g)"),
  "servings_per_pack": number of servings per container, or null,
  "ingredients": the full ingredients list as printed, or "",
  "nutrients": {{ nutrient: {{"amount": number, "dv": % daily value as a number or null}} }}
               using only these keys and units, per serving: {NUTRIENT_LIST},
  "abv": alcohol by volume in percent for alcoholic drinks, else null
}}"""

LABEL_PROMPT = f"""These photos show the packaging of ONE food or drink product: front, ingredients
list and/or Nutrition Facts panel. Read exactly what is printed. Never guess values that are not
visible; use null for anything you cannot read. Return only JSON:
{INFO_SHAPE}"""

SEARCH_PROMPT = """Find the official ingredients list and Nutrition Facts for this exact US grocery
product: brand "{brand}", product "{name}"{store}. Use Google Search and prefer the maker's or the
retailer's own product page. It must be this exact brand and product (same flavor and variety);
if you cannot find it, set "found" to false. Never fill in values from a different product.
Return only JSON, no other text:
{shape}"""


class NotFound(Exception):
    pass


# ---------------------------------------------------------------- barcodes

def read_barcode(jpeg):
    """Digits of the first product barcode in a photo, or None."""
    if not BARCODES_AVAILABLE:
        return None
    img = Image.open(io.BytesIO(jpeg))
    for result in zxingcpp.read_barcodes(img):
        text = (result.text or "").strip()
        if text.isdigit() and len(text) in (8, 12, 13, 14):
            return text
    return None


# ---------------------------------------------------------------- Open Food Facts

def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def off_by_barcode(barcode):
    data = _get_json(f"https://world.openfoodfacts.org/api/v2/product/{barcode}.json?fields={OFF_FIELDS}")
    product = data.get("product")
    if data.get("status") != 1 or not product or not product.get("product_name"):
        raise NotFound(barcode)
    return _from_off(product, f"https://world.openfoodfacts.org/product/{barcode}")


def off_by_name(brand, name):
    """Best Open Food Facts match for brand + name, only if the brand matches exactly."""
    if not brand.strip():
        raise NotFound(name)
    q = urllib.parse.quote(f"{brand} {name}")
    data = _get_json(f"https://search.openfoodfacts.org/search?q={q}&page_size=20&fields=code,product_name,brands")
    wanted = _words(name) - _words(brand)
    best, best_score = None, 0
    for hit in data.get("hits", []):
        brands = hit.get("brands") or []
        brands = brands if isinstance(brands, list) else [brands]
        if not any(_norm(b) == _norm(brand) for b in brands):
            continue
        score = len(wanted & _words(hit.get("product_name", "")))
        if score > best_score:
            best, best_score = hit, score
    if best is None or best_score < max(1, len(wanted) // 2):
        raise NotFound(name)
    # The search index has no ingredients, so fetch the full product record.
    info = off_by_barcode(best["code"])
    if not info["ingredients"]:
        raise NotFound(name)
    return info


def _from_off(p, url):
    brands = p.get("brands") or ""
    brand = ", ".join(brands) if isinstance(brands, list) else brands
    nutriments = p.get("nutriments") or {}
    grams = _num(p.get("serving_quantity"))
    nutrients = {}
    for off_key, (key, factor) in OFF_NUTRIENTS.items():
        value = _num(nutriments.get(f"{off_key}_serving"))
        if value is None and grams:
            per_100 = _num(nutriments.get(f"{off_key}_100g"))
            value = per_100 * grams / 100 if per_100 is not None else None
        if value is not None:
            nutrients[key] = {"amount": round(value * factor, 2), "dv": None}
    return _info(
        source="Open Food Facts", url=url, name=p.get("product_name", ""), brand=brand,
        barcode=p.get("code", ""), serving_size=p.get("serving_size") or "",
        servings_per_pack=None, ingredients=p.get("ingredients_text") or "",
        nutrients=nutrients, abv=None, photo_url=p.get("image_front_url") or "",
        quantity=p.get("quantity") or "",
    )


# ---------------------------------------------------------------- Gemini

def web_lookup(model, brand, name, store=""):
    prompt = SEARCH_PROMPT.format(brand=brand, name=name, shape=INFO_SHAPE,
                                  store=f", sold at {store}" if store else "")
    result, sources = gemini.search_json("product_web_lookup", model, prompt)
    if not result.get("found"):
        raise NotFound(name)
    return _from_ai(result, "Web search", sources[0][1] if sources else "", sources)


def read_label(model, jpegs):
    result = gemini.generate_json("label_read", model, LABEL_PROMPT, [(j, "image/jpeg") for j in jpegs])
    return _from_ai(result, "Label photo", "", [])


def _from_ai(r, source, url, sources):
    nutrients = {}
    for key, v in (r.get("nutrients") or {}).items():
        if key in NUTRIENT_UNITS and isinstance(v, dict) and _num(v.get("amount")) is not None:
            nutrients[key] = {"amount": _num(v.get("amount")), "dv": _num(v.get("dv"))}
    info = _info(
        source=source, url=url, name=r.get("name") or "", brand=r.get("brand") or "", barcode="",
        serving_size=r.get("serving_size") or "", servings_per_pack=_num(r.get("servings_per_pack")),
        ingredients=r.get("ingredients") or "", nutrients=nutrients, abv=_num(r.get("abv")),
        photo_url="", quantity="",
    )
    info["sources"] = sources
    return info


# ---------------------------------------------------------------- shared

def _info(**fields):
    for key, n in fields["nutrients"].items():
        if n.get("dv") is None and key in DAILY_VALUES:
            n["dv"] = round(100 * n["amount"] / DAILY_VALUES[key])
    fields.setdefault("sources", [])
    return fields


def product_columns(info):
    """The products-tab columns this info fills in."""
    return {
        "serving_size": info.get("serving_size", ""),
        "servings_per_pack": info.get("servings_per_pack") or "",
        "ingredients": info.get("ingredients", ""),
        "nutrients_json": json.dumps({"per_serving": info.get("nutrients", {})}) if info.get("nutrients") else "",
        "abv": info.get("abv") or "",
        "photo_url": info.get("photo_url", ""),
        "info_source": info.get("source", ""),
        "info_url": info.get("url", ""),
        "info_status": "confirmed",
    }


def summary(info):
    """A few readable lines for a 'possible match' card."""
    lines = []
    if info.get("serving_size"):
        per = f" · {info['servings_per_pack']:g} per pack" if info.get("servings_per_pack") else ""
        lines.append(f"Serving: {info['serving_size']}{per}")
    n = info.get("nutrients", {})
    picks = [("calories", "Calories"), ("sodium", "Sodium"), ("added_sugars", "Added sugar"),
             ("saturated_fat", "Sat. fat"), ("protein", "Protein")]
    parts = [f"{label} {n[k]['amount']:g}{NUTRIENT_UNITS[k] if k != 'calories' else ''}"
             + (f" ({n[k]['dv']:g}%)" if n[k].get("dv") is not None and k != "calories" else "")
             for k, label in picks if k in n]
    if parts:
        lines.append(" · ".join(parts))
    if info.get("abv"):
        lines.append(f"Alcohol: {info['abv']:g}%")
    return lines


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _norm(text):
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def _words(text):
    return {w for w in re.findall(r"[a-z0-9]+", str(text).lower()) if len(w) > 2}
