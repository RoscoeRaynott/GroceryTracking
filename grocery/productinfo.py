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
import urllib.error
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
product. Everything the user told us about it:
  {query}
Step 1: find the exact product. Search Google with ALL of these details (store, pack size, cut,
variety), not a generic search. Costco and Costco Business Center sell the same products, so check
costco.com and costcobusinessdelivery.com; for other stores use their own site or the maker's page.
The user's words may be informal or regional, so match on store, pack size and cut rather than exact
wording (e.g. in South Asian usage "mutton" usually means goat meat).
Step 2: read that product page (or the maker's page) for its ingredients list and Nutrition Facts.
Raw single-ingredient meat, fish and produce often list none; that is fine, just leave them empty.
Never fill in values from a different product.
Return only JSON, no other text:
{{
  "product_found": true if you found this exact product,
  "matched_name": the product's full name as listed, "matched_url": its product page URL,
  "image_url": the direct URL of the product's main photo if you saw one, else "",
  "has_label_info": true only if you found its ingredients or Nutrition Facts,
  "pack_text": the pack size exactly as the page lists it (e.g. "18 count", "12 oz", "2 x 1 gal"), or "",
  "pack_in_unit": how many {unit} are in ONE pack, converting if needed (12 oz = 0.75 lb;
                  "2 x 1 gal" = 2 gal), or null if it can't be expressed in {unit},
  "reason": one sentence on what you found or why not,
  "info": {shape}
}}"""


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

def web_lookup(model, query, unit="each"):
    """query = everything known about the product (brand, name, size, store, what was said).
    unit = the item's standard unit, so the pack size comes back in it."""
    prompt = SEARCH_PROMPT.format(query=query, shape=INFO_SHAPE, unit=unit)
    result, sources = gemini.search_json("product_web_lookup", model, prompt)
    if not result.get("product_found"):
        raise NotFound(result.get("reason") or "No exact match found.")
    info = _from_ai(dict(result.get("info") or {}, name=result.get("matched_name") or ""), "Web search",
                    # The pages Google actually returned are reliable; a URL the model types itself may be made up.
                    sources[0][1] if sources else result.get("matched_url") or "", sources)
    info["no_label_info"] = not result.get("has_label_info") or not (info["ingredients"] or info["nutrients"])
    info["photo_url"] = page_image(info["url"]) or checked_image(result.get("image_url"))
    info["reason"] = result.get("reason", "")
    info["pack_text"] = result.get("pack_text") or ""
    pack = _num(result.get("pack_in_unit"))
    info["pack_units"] = pack if pack and pack > 0 else None
    info["pack_unit"] = unit
    return info


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
    """The products-tab columns this info fills in (an empty photo never erases one already saved)."""
    cols = {
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
    if not cols["photo_url"]:
        del cols["photo_url"]
    if info.get("pack_units"):
        cols["pack_size"], cols["pack_unit"] = info["pack_units"], info.get("pack_unit", "")
    return cols


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


# ---------------------------------------------------------------- photos

def thumbnail(jpeg, size=160):
    """A tiny JPEG (a few KB) as a data URI, small enough to keep in a Sheet cell."""
    import base64
    img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    img.thumbnail((size, size))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=70)
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode("ascii")


def checked_image(url):
    """Use an image URL only if it really is an image (a URL the model typed may be made up)."""
    if not url or not str(url).startswith("http"):
        return ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=8) as resp:
            ok = resp.status == 200 and resp.headers.get("Content-Type", "").startswith("image/")
        return url if ok else ""
    except Exception:
        return ""


def page_image(url):
    """Best effort: the preview picture a web page declares (og:image). Many store sites block this."""
    if not url:
        return ""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel) AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/124.0 Mobile Safari/537.36"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read(400_000).decode("utf-8", "replace")
    except Exception:
        return ""
    for pattern in (r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
                    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image'):
        m = re.search(pattern, html, re.I)
        if m and m.group(1).startswith("http"):
            return m.group(1)
    return ""


class TryLater(Exception):
    pass


def wiki_image(*titles):
    """A generic picture for an item from its English Wikipedia article (free, no key).

    Tries each title in turn. Returns the image URL, "" if none of the articles has one,
    or raises TryLater when Wikipedia is busy (so the lookup is retried another time).
    """
    for title in [t for t in titles if t and str(t).strip()]:
        params = urllib.parse.urlencode({"action": "query", "prop": "pageimages", "format": "json",
                                         "piprop": "thumbnail", "pithumbsize": 200, "redirects": 1,
                                         "titles": str(title).strip()})
        try:
            data = _get_json(f"https://en.wikipedia.org/w/api.php?{params}")
        except urllib.error.HTTPError as ex:
            if ex.code == 429:
                raise TryLater() from ex
            continue
        except Exception as ex:
            raise TryLater() from ex
        for page in data.get("query", {}).get("pages", {}).values():
            src = page.get("thumbnail", {}).get("source")
            if src:
                return src
    return ""
