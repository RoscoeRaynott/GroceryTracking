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
visible; use null for anything you cannot read.
If the front of the pack is shown, "name" is the FULL product name exactly as printed, with its variety or flavour
(e.g. "Chicken White Karahi Masala", not just "Masala"), and "brand" is the brand.
The label may be in any language (Bengali, Hindi, Arabic, Chinese…): read it and write everything in English.
If the nutrition table gives values per 100 g (or 100 ml) instead of per serving, set "serving_size" to
"100 g" (or "100 ml") and give the amounts per 100 g. Give "dv" only where a % daily value is printed,
else null. Return only JSON:
{INFO_SHAPE}"""

SEARCH_PROMPT = """Identify this grocery product and, if published, read its ingredients and Nutrition Facts.
What we know about it:
  {query}
{barcode_rule}
Goal 1, the one that matters most: FIND THE PRODUCT.
- Use a few SHORT searches (brand + product, e.g. "Radhuni turmeric powder"), not one long search.
- Search anywhere: the maker's own site, Amazon, Walmart, and South Asian / East Asian / international
  grocery sites. Many imported products (Indian, Bangladeshi, Asian brands) are not sold by big US chains.
{store_rule}- If the brand makes only one version of this product (one turmeric powder, one garam masala), that
  IS the product, even if the size differs from what we know; report the size you found.
- The user's words may be informal or regional (e.g. in South Asian usage "mutton" usually means goat).
Goal 2, a bonus: its ingredients list and Nutrition Facts, from that product's page or the maker's page.
Many spices, imported foods and raw meat/fish/produce publish none. That is fine: still report the
product as found and leave the label info empty. Never fill in values from a different product.
Return only JSON, no other text:
{{
  "product_found": true if you identified the product (label info not required),
  "matched_name": the product's full name as listed, "matched_url": its product page URL,
  "matched_brand": the brand as printed on the product or its page (e.g. "Kirkland Signature"), or "",
  "image_url": the direct URL of the product's main photo if you saw one, else "",
  "has_label_info": true only if you found its ingredients or Nutrition Facts,
  "pack_text": the pack size exactly as the page lists it (e.g. "18 count", "200 g", "2 x 1 gal"), or "",
  "size_note": if the size you found differs from what we know, say so briefly (e.g. "found 400 g; you had 200 g"), else "",
  "pack_count": how many separate units are in ONE pack as sold (a 2-pack of tubs = 2, 6 x 1 gal = 6,
                a single item = 1),
  "each_in_unit": how many {unit} are in ONE of those units, converting to {unit} (12 oz = 0.75 lb;
                  "32 oz" tub = 2 lb; "16 fl oz" = 473 ml; 1 gal jug = 1 gal), or null if it can't be expressed in {unit},
  "pack_in_unit": pack_count x each_in_unit = how many {unit} are in ONE whole pack as sold, or null,
  "reason": one sentence on what you found or why not,
  "info": {shape}
}}"""

STORE_RULE = """- This product is sold by {store}. FIRST find it on {store}'s own website ({sites}) and take the
  name and the pack exactly as {store} sells it (e.g. a 2-pack of 32 oz tubs, a case of 6 x 1 gal).
  Use other sites only if {store}'s own page can't be found, and then say so in "reason".
"""
STORE_SITES = {"Costco": "costco.com, costcobusinessdelivery.com", "Sam's Club": "samsclub.com",
               "Walmart": "walmart.com", "Target": "target.com", "Kroger": "kroger.com", "Safeway": "safeway.com",
               "Albertsons": "albertsons.com", "Randalls": "randalls.com", "H-E-B": "heb.com",
               "Whole Foods": "wholefoodsmarket.com", "Trader Joe's": "traderjoes.com", "Aldi": "aldi.us",
               "Sprouts": "sprouts.com", "Central Market": "centralmarket.com", "Fiesta": "fiestamart.com"}
GENERAL_RULE = """- Imported or non-chain products: search anywhere (maker, Amazon, international grocery sites).
"""

BARCODE_RULE = """Its barcode (UPC/EAN) is {barcode}. Search for this number first: shopping sites often list
imported products by barcode. Only report a product whose listing matches this barcode or clearly is it."""


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
    try:
        data = _get_json(f"https://world.openfoodfacts.org/api/v2/product/{barcode}.json?fields={OFF_FIELDS}")
    except urllib.error.HTTPError as ex:
        if ex.code == 404:  # Open Food Facts answers 404 for barcodes it doesn't have
            raise NotFound(barcode) from ex
        raise
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

def by_barcode(model, barcode, query="", unit="each", store=""):
    """Open Food Facts first (free); if it doesn't know the barcode, search Google for the number."""
    try:
        return off_by_barcode(barcode)
    except NotFound:
        return web_lookup(model, query, unit, barcode=barcode, store=store)


def web_lookup(model, query, unit="each", barcode="", store=""):
    """query = short search text (brand, product, real size, big-chain store).
    unit = the item's standard unit, so the pack size comes back in it.
    barcode = digits to search for first (used when Open Food Facts doesn't know the barcode)."""
    store_rule = (STORE_RULE.format(store=store, sites=STORE_SITES.get(store, f"{store}'s site")) if store
                  else GENERAL_RULE)
    prompt = SEARCH_PROMPT.format(query=query or f"barcode {barcode}", shape=INFO_SHAPE, unit=unit,
                                  store_rule=store_rule,
                                  barcode_rule=BARCODE_RULE.format(barcode=barcode) if barcode else "")
    try:
        result, sources = gemini.search_json("product_web_lookup", model, prompt)
    except gemini.GeminiError as ex:
        if "RECITATION" not in str(ex):
            raise
        # Google blocked the answer for copying the label text word for word: ask for it in three small parts.
        result, sources = _lookup_in_parts(model, query or f"barcode {barcode}", unit, store_rule,
                                           BARCODE_RULE.format(barcode=barcode) if barcode else "")
    if not result.get("product_found"):
        raise NotFound(result.get("reason") or "No exact match found.")
    info = _from_ai(dict(result.get("info") or {}, name=result.get("matched_name") or ""), "Web search",
                    # The pages Google actually returned are reliable; a URL the model types itself may be made up.
                    sources[0][1] if sources else result.get("matched_url") or "", sources)
    info["no_label_info"] = not result.get("has_label_info") or not (info["ingredients"] or info["nutrients"])
    info["photo_url"] = page_image(info["url"]) or checked_image(result.get("image_url"))
    info["reason"] = result.get("reason", "")
    info["pack_text"] = result.get("pack_text") or ""
    info["size_note"] = result.get("size_note") or ""
    info["brand"] = info.get("brand") or result.get("matched_brand") or ""
    if barcode:
        info["barcode"] = barcode
    count, each = _num(result.get("pack_count")), _num(result.get("each_in_unit"))
    if unit == "each" and count and not each:
        each = 1.0  # counted in "each": a 2-pack is 2 each, whatever size each one is
    pack = _num(result.get("pack_in_unit")) or (count * each if count and each else None)
    info["pack_units"] = pack if pack and pack > 0 else None
    info["pack_count"] = count if count and count >= 1 else None
    info["pack_unit"] = unit
    return info


def read_label(model, jpegs):
    result = gemini.generate_json("label_read", model, LABEL_PROMPT, [(j, "image/jpeg") for j in jpegs])
    return _from_ai(result, "Label photo", "", [])


def with_label(found, label):
    """A web/barcode match plus what the label photo says: the label wins for ingredients and nutrition."""
    out = dict(found)
    out["from_photo"] = []
    for key in ("serving_size", "servings_per_pack", "ingredients", "nutrients", "abv"):
        if label.get(key):
            out[key] = label[key]
            out["from_photo"].append("nutrients_json" if key == "nutrients" else key)
    out["source"] = f"{found.get('source') or 'Web search'} + label photo"
    out["no_label_info"] = not (out.get("ingredients") or out.get("nutrients"))
    return out


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
        cols["pack_count"] = info.get("pack_count") or ""
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


# ---------------------------------------------------------------- lookup lab (trial searches, nothing saved)

LAB_PROMPTS = {
    "product": """Identify this grocery product. What we know: {query}
{store_rule}Use a few short searches. Return only JSON:
{{"product_found": true/false, "matched_name": full name as listed, "matched_brand": brand or "",
  "matched_url": product page URL, "image_url": direct URL of its main photo or "",
  "pack_text": pack size as listed or "", "pack_count": how many units in one pack (2-pack = 2),
  "each_in_unit": {unit} in one of those units, or null, "reason": one sentence}}""",
    "nutrition": """Find the Nutrition Facts for this grocery product. What we know: {query}
{store_rule}Use a few short searches. Report only numbers, never copy text. Return only JSON:
{{"found": true/false, "serving_size": short, e.g. "3/4 cup (170g)", "servings_per_pack": number or null,
  "nutrients": {{nutrient: {{"amount": number, "dv": number or null}}}} using only these keys and units, per serving:
  {nutrients}, "reason": one sentence}}""",
    "ingredient_items": """Find what this grocery product is made of. What we know: {query}
{store_rule}Use a few short searches. Do NOT copy the printed ingredients sentence. Instead list each
ingredient separately as a short lowercase name in its own array item, in the same order
(e.g. ["chicken wings", "salt", "garlic", "butter"]). Return only JSON:
{{"found": true/false, "ingredient_items": [ ... ], "reason": one sentence}}""",
    "ingredients": """Find the ingredients list for this grocery product. What we know: {query}
{store_rule}Use a few short searches. Return only JSON:
{{"found": true/false, "ingredients": the ingredients as one comma-separated list, or "",
  "reason": one sentence}}""",
}


def _lookup_in_parts(model, query, unit, store_rule, barcode_rule=""):
    """Product only + nutrition only + ingredients as separate items, at the same time, combined into the
    usual lookup reply. Only the product part must succeed; the other two are a bonus."""
    from concurrent.futures import ThreadPoolExecutor
    try:  # keep the app's session attached to the worker threads (their AI calls are logged)
        from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx
        ctx = get_script_run_ctx()
        start = (lambda: add_script_run_ctx(ctx=ctx)) if ctx else None
    except ImportError:
        start = None
    rule = store_rule + barcode_rule + ("\n" if barcode_rule else "")

    def run(part):
        prompt = LAB_PROMPTS[part].format(query=query, store_rule=rule, unit=unit, nutrients=NUTRIENT_LIST)
        try:
            return gemini.search_json(f"lookup_{part}", model, prompt)
        except gemini.GeminiError:
            return ({}, [])

    with ThreadPoolExecutor(max_workers=3, initializer=start) as pool:
        product, nutrition, items = pool.map(run, ["product", "nutrition", "ingredient_items"])
    (p, sources), (n, n_src), (i, i_src) = product, nutrition, items
    ingredients = []
    for x in i.get("ingredient_items") or []:
        x = str(x).strip()
        if x and x.lower() not in {y.lower() for y in ingredients}:
            ingredients.append(x)  # sub-recipes repeat salt, sugar…: keep the first
    info = {"serving_size": n.get("serving_size") or "", "servings_per_pack": n.get("servings_per_pack"),
            "nutrients": n.get("nutrients") or {}, "ingredients": ", ".join(ingredients)}
    reply = dict(p, info=info, has_label_info=bool(info["nutrients"] or ingredients),
                 reason=(p.get("reason") or "") + " (Label text was blocked, so it was fetched in parts.)")
    return reply, sources + [s for s in n_src + i_src if s not in sources]


def lab_lookup(model, part, query, unit="each", store=""):
    """One focused trial search: part = "product" | "nutrition" | "ingredients" | "ingredient_items".
    Returns (reply, sources)."""
    store_rule = (STORE_RULE.format(store=store, sites=STORE_SITES.get(store, f"{store}'s site")) if store
                  else GENERAL_RULE)
    prompt = LAB_PROMPTS[part].format(query=query, store_rule=store_rule, unit=unit, nutrients=NUTRIENT_LIST)
    return gemini.search_json(f"lab_{part}", model, prompt)


def identify_then_search(model, found, unit="each", store=""):
    """A barcode or a photo of the pack told us exactly what it is: search the web with that exact name for the
    photo, pack size and label info. What a photo of the label read always wins; the barcode is kept."""
    name = str(found.get("name") or "").strip()
    if not name:
        return found
    brand = str(found.get("brand") or "").strip()
    query = name if brand.lower() in name.lower() else f"{brand} {name}".strip()
    try:
        web = web_lookup(model, query, unit, barcode=found.get("barcode", ""), store=store)
    except Exception:
        return found  # the identification alone is still worth showing
    from_label = "label photo" in str(found.get("source", "")).lower()
    merged = with_label(web, found) if from_label else dict(web)
    merged["barcode"] = found.get("barcode") or web.get("barcode", "")
    merged["brand"] = brand or web.get("brand", "")
    if from_label:
        merged["name"] = name  # printed on the pack in your hand
    return merged
