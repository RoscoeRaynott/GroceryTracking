"""USDA FoodData Central: typical nutrition for foods without a label (fruit, vegetables, raw meat, fish).

The key lives in Streamlit secrets as USDA_API_KEY and is sent in a header, never in the URL.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

import streamlit as st

API = "https://api.nal.usda.gov/fdc/v1"


def api_key():
    """USDA_API_KEY from Streamlit secrets, wherever it sits (a line under a [section] belongs to that section)."""
    def walk(node):
        try:
            items = node.items()
        except Exception:
            return None
        for k, v in items:
            if str(k).lower() == "usda_api_key" and isinstance(v, str) and v.strip():
                return v.strip()
        for _, v in items:
            found = walk(v)
            if found:
                return found
        return None
    try:
        return walk(st.secrets)
    except Exception:
        return None


def _get(path, params=None):
    key = api_key()
    if not key:
        raise RuntimeError("No USDA_API_KEY in Streamlit secrets.")
    url = f"{API}/{path}" + (f"?{urllib.parse.urlencode(params, doseq=True)}" if params else "")
    req = urllib.request.Request(url, headers={"X-Api-Key": key, "User-Agent": "GroceryTracking/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as ex:
        detail = ex.read().decode("utf-8", "replace")[:300].replace(key, "•••")
        raise RuntimeError(f"USDA returned HTTP {ex.code}: {detail}") from ex


def search(query, page_size=3):
    """Generic foods (not branded) matching the query, best first."""
    return _get("foods/search", {"query": query, "pageSize": page_size,
                                 "dataType": ["Foundation", "SR Legacy"]})


def food(fdc_id):
    """One food in full, including its typical portions (e.g. 1 medium = 118 g)."""
    return _get(f"food/{fdc_id}")


# USDA nutrient name -> (our nutrient key, the USDA unit it must be in). Values are per 100 g.
NUTRIENTS = {
    "Energy": ("calories", "KCAL"), "Total lipid (fat)": ("total_fat", "G"),
    "Fatty acids, total saturated": ("saturated_fat", "G"), "Fatty acids, total trans": ("trans_fat", "G"),
    "Cholesterol": ("cholesterol", "MG"), "Sodium, Na": ("sodium", "MG"),
    "Carbohydrate, by difference": ("total_carbohydrate", "G"), "Fiber, total dietary": ("dietary_fiber", "G"),
    "Sugars, total including NLEA": ("total_sugars", "G"), "Sugars, Total": ("total_sugars", "G"),
    "Total Sugars": ("total_sugars", "G"), "Sugars, added": ("added_sugars", "G"), "Protein": ("protein", "G"),
    "Vitamin D (D2 + D3)": ("vitamin_d", "UG"), "Calcium, Ca": ("calcium", "MG"), "Iron, Fe": ("iron", "MG"),
    "Potassium, K": ("potassium", "MG"),
}
# Forms you didn't buy unless you said so (USDA's first hit for "banana" is banana powder).
OTHER_FORMS = ("dehydrated", "dried", "powder", "canned", "cooked", "juice", "frozen", "baby food", "chips", "fried",
               "roasted", "boiled", "baked", "braised", "stewed", "sweetened", "syrup", "candied", "pickled", "smoked")


def per_100g(food):
    """Our nutrients, per 100 g, from one USDA search hit."""
    out = {}
    for n in food.get("foodNutrients") or []:
        name, unit, value = n.get("nutrientName"), str(n.get("unitName") or "").upper(), n.get("value")
        if name in NUTRIENTS and value is not None:
            key, want = NUTRIENTS[name]
            if unit == want and key not in out:
                out[key] = {"amount": round(float(value), 2)}
    return out


def _stem(word):
    word = word.lower().strip(",")
    return word[:-3] + "y" if word.endswith("ies") else word[:-2] if word.endswith("oes") else word.rstrip("s")


def matches(item_name, limit=6):
    """Best USDA foods for a loose item (raw first, other forms last): [{fdc_id, name, nutrients}]."""
    wanted = item_name.lower()
    foods = search(f"{item_name} raw", page_size=12).get("foods") or []
    first = _stem(item_name.split()[0]) if item_name.split() else ""

    def score(f):
        desc = (f.get("description") or "").lower()
        s = 0
        if "raw" in desc and "raw" not in wanted:
            s += 3
        if any(w in desc and w not in wanted for w in OTHER_FORMS):
            s -= 5
        if first and _stem(desc.split()[0]) == first:
            s += 3
        return s - len(desc) / 100  # shorter, plainer names first

    ranked = sorted(foods, key=score, reverse=True)
    return [{"fdc_id": f["fdcId"], "name": f.get("description", ""), "nutrients": per_100g(f)}
            for f in ranked[:limit] if per_100g(f)]


def piece_grams(fdc_id):
    """Typical weight of one piece (a medium banana = 118 g), or None. Also returns the portion wording."""
    portions = food(fdc_id).get("foodPortions") or []
    named = [(str(p.get("modifier") or p.get("portionDescription") or ""), p.get("gramWeight")) for p in portions
             if p.get("gramWeight")]
    for want in ("medium", "large", "small", "fruit", "whole", "each", "piece"):
        for label, grams in named:
            if want in label.lower() and "cup" not in label.lower() and "slice" not in label.lower():
                return round(float(grams), 1), label
    return None, ""
