"""Ingredient names tidied once per product (so "cane sugar" and "organic sugar" count as sugar), and the watch
list. Ingredient lists have no amounts, so Insights shows how often an ingredient appears, not how much."""

import json
import re

from grocery import gemini, records, sheets

WATCH_DEFAULT = ("high fructose corn syrup, palm oil, artificial colors, added sugar, hydrogenated oils, "
                 "MSG, sodium nitrite")

PROMPT = """Tidy the ingredient lists of these grocery products for a personal nutrition overview.
For each product, list its ingredients as short, generic, lowercase names, one per ingredient, merging variants:
"organic cane sugar" -> "sugar", "enriched wheat flour (wheat flour, niacin, iron…)" -> "wheat flour",
"sea salt" -> "salt", "red 40" stays "red 40". Drop percentages, "contains 2% or less of", and allergen notes.
Also say which of these WATCH LIST entries the product contains (use the watch-list wording exactly; artificial
colors include Red 40, Yellow 5, Blue 1 and similar; added sugar includes sugar, cane sugar, corn syrup, dextrose…):
WATCH LIST: {watch}
PRODUCTS (ref | product | ingredients as printed):
{products}
Be careful: only list what is actually printed, never guess. Natural sugars in fruit, milk or vegetables are not
added sugar; "sugar snap peas" is a vegetable; "no added sugar" means none. If unsure whether a watch-list entry
applies, leave it out.
Return only JSON: {{"ref": {{"items": [names], "watch": [watch-list entries it contains]}}, ...}}"""


def watch_list():
    text = sheets.get_settings().get("watch_list") or WATCH_DEFAULT
    return [w.strip() for w in text.split(",") if w.strip()]


ALIASES = {"artificial colors": ("red 40", "red 3", "yellow 5", "yellow 6", "blue 1", "blue 2", "green 3",
                                 "artificial color", "fd&c"),
           "added sugar": ("sugar", "corn syrup", "dextrose", "sucrose", "fructose", "cane juice", "molasses", "maltose"),
           "hydrogenated oils": ("hydrogenated",), "msg": ("monosodium glutamate", "msg"),
           "high fructose corn syrup": ("high fructose corn syrup", "hfcs")}


NOT_ADDED = ("sugar snap", "sugar-free", "sugar free", "no added sugar", "no sugar added", "without added sugar",
             "unsweetened")


def rough(product):
    """Simple word matching on the printed list, used until (or if) the AI tidying hasn't happened, so the
    counts are never falsely zero: {"items": [...], "watch": [...]}."""
    text = str((product or {}).get("ingredients") or "")
    items = [re.sub(r"\s+", " ", re.sub(r"[\(\[].*?[\)\]]|\d+%|contains.*", "", x, flags=re.I)).strip(" .*").lower()
             for x in re.split(r",|;", text)]
    items = [x for x in items if x and len(x) < 40]
    low = text.lower()
    for phrase in NOT_ADDED:  # "sugar snap peas", "sugar-free", "no added sugar" aren't added sugar
        low = low.replace(phrase, " ")
    watch = [w for w in watch_list() if any(re.search(rf"\b{re.escape(a)}\b", low) for a in ALIASES.get(w.lower(), (w.lower(),)))]
    return {"items": items, "watch": watch, "rough": True}


def parsed(product):
    """{"items": [...], "watch": [...]} saved for this product, or None if not tidied yet."""
    try:
        data = json.loads((product or {}).get("ingredients_norm") or "")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def tidy_new(model, limit=40):
    """One cheap AI call that tidies the ingredient lists of products not tidied yet. Returns how many."""
    todo = [p for p in records.products() if p.get("ingredients") and parsed(p) is None][:limit]
    if not todo:
        return 0
    refs = {f"p{n}": p for n, p in enumerate(todo, start=1)}
    lines = "\n".join(f"{ref} | {p['name']} | {re.sub(r'\s+', ' ', p['ingredients'])[:1200]}" for ref, p in refs.items())
    try:
        reply = gemini.generate_json("ingredients_tidy", model, PROMPT.format(watch=", ".join(watch_list()), products=lines))
    except Exception:
        return 0
    change = {}
    for ref, p in refs.items():
        got = reply.get(ref) if isinstance(reply, dict) else None
        if isinstance(got, dict):
            items = [str(x).strip().lower() for x in got.get("items") or [] if str(x).strip()]
            watch = [w for w in watch_list() if any(w.lower() == str(x).strip().lower() for x in got.get("watch") or [])]
            change[p["product_id"]] = {"ingredients_norm": json.dumps({"items": items, "watch": watch})}
    if change:
        sheets.update_rows("products", "product_id", change)
    return len(change)
