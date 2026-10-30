"""The shared voice engine: one call turns what you said into actions.

Used by both "Voice update" and "Add purchase → Say it" so they can later be
merged into one central voice button. Gemini hears the audio directly (or
reads typed text), sees your open batches and recent purchases, and returns
actions; nothing is saved until you approve the preview.
"""

from grocery import gemini, records, sheets, timeutil
from grocery.schema import KINDS, STORAGE, UNITS

PROMPT = """You are the voice assistant of a personal grocery tracker for one person in Houston, Texas.
Today is {today} ({weekday}). Dates are relative to today: "3 days ago" = today minus 3 days.

{heard}

OPEN BATCHES (things the user currently has; ref | item | product | store | bought on | quantity | already wasted):
{batches}

RECENT PURCHASES (ref | item | product | store | quantity | price | bought on):
{history}

KNOWN ITEMS (generic item names): {items}

KNOWN PRODUCTS with the user's own nicknames (ref | item | product | one pack as sold | nicknames). When the
user uses a nickname or a close variant ("Costco egg whites", "wholesale mutton"), it means that product:
{products}

Work out every action the user wants. Possible actions:
- "waste": part or all of a batch went bad / was thrown away. "fraction" = share of the WHOLE batch
  as bought (2 avocados of a 6-pack = 0.3333; "half the spinach" = 0.5; "a third" = 0.3333).
  Reporting waste normally means the batch is done: the wasted part went bad and the rest was eaten,
  so set "batch_done": true. Set it to false ONLY if the user explicitly says some is still left
  or salvageable (e.g. "one bell pepper got fungus, the other two are fine", "two slices of cheese
  went bad, the rest is still good", "200 grams wasted, 500 grams still left"). When they give
  amounts, "fraction" is still the wasted share of the WHOLE batch as bought.
- "finish": a batch is used up. "rest_wasted": true if they say the remainder was thrown away.
- "expiry": the user says when a batch expires. "expiry_date" as YYYY-MM-DD.
- "delete": the user wants a batch removed (e.g. "delete the avocados I added yesterday").
- "add": the user bought something. Use RECENT PURCHASES to fill in product, quantity and price when
  they refer to a usual purchase ("the 2-gallon milk from Costco"); "based_on" = that ref.
{packs_rule}
{mode_rule}
Choosing batches: match by generic item (an avocado is an avocado). If several open batches match,
pick the MOST RECENTLY BOUGHT one. Only ask a question when you truly cannot tell which item they
mean, or you could not hear a part clearly. Never invent purchases or quantities.

Return only JSON:
{{
  "transcript": what the user said, word for word,
  "actions": [
    {{"type": "waste", "batch": ref, "fraction": number, "batch_done": true/false, "happened_on": "YYYY-MM-DD",
      "said": short quote}},
    {{"type": "finish", "batch": ref, "happened_on": "YYYY-MM-DD", "rest_wasted": true/false, "said": short quote}},
    {{"type": "expiry", "batch": ref, "expiry_date": "YYYY-MM-DD", "said": short quote}},
    {{"type": "delete", "batch": ref, "said": short quote}},
    {{"type": "add", "item": generic item name (an existing one if it matches), "is_new": true/false,
      "store": store exactly as said (e.g. "Costco Business Center") or null,
      "product": FULL product description with every detail the user said: brand, cut, variety,
                 pack size, where it is sold (e.g. "Costco Business Center whole mutton, 15 lb"),
      "packs": number of packs as sold, or null, "units": number or null, "unit": one of {units},
      "price": dollars or null if unknown, "price_is_total": true/false,
      "based_on": purchase ref or null, "purchased_on": "YYYY-MM-DD",
      "product_ref": known product ref if it is one of KNOWN PRODUCTS, else null,
      "said_name": the exact words the user used for this product,
      "kind": one of {kinds}, "storage": one of {storage}, "perishable": true/false, "emoji": one emoji or "",
      "wiki": the English Wikipedia article title that best pictures this generic item (e.g. "Egg white",
              "Catla" for katla fish, "Ilish" for hilsa, "Half and half"), or "",
      "packaged": true/false, "said": short quote}}
  ],
  "questions": [
    {{"text": a short question for the user, "options": [{{"label": short answer, "batch": ref or null}}],
      "action": the action this answer completes, with "batch" left null}}
  ],
  "not_understood": anything you could not map to an action, or ""
}}"""

PACKS_RULE = """  Amounts: when the user counts PACKAGES of something sold in a package ("5 crates of Costco water",
  "2 egg whites", "a case of sparkling water", "3 bottles of shampoo"), set "packs" to that count and
  "units" to null; the app multiplies by the known pack size. When they give an amount in a measuring
  unit ("2 gallons", "3 lb", "500 ml") or count loose things ("6 avocados", "3 bell peppers"), set
  "units" and "unit" and leave "packs" null.
  Prices: a price said for packs is the price of ONE pack as sold (like a receipt line per pack), so
  "price_is_total" is false; set it true only when the user clearly says it is for all of them
  ("25 dollars total", "altogether", "for all five").
  Unit for a new item: "ml" for liquids (egg whites, juice, oil, sauces), except "gal" for drinking
  water and milk; "each" for countable things, "lb" for things sold by weight, "loads" for detergent."""


MODE_RULES = {
    "any": "",
    "update": "The user is on the 'update' screen, but still report any 'add' they clearly mean.",
    "add": "The user is on the 'add purchase' screen: everything they list is an 'add' unless they clearly say otherwise.",
}


def understand(model, audio=None, text=None, mode="any"):
    """Returns (result, batch_refs, history_refs) where refs map "b1"/"h1" to Sheet rows."""
    batches = [b for b in sheets.read_tab("batches") if b.get("status") == "open"]
    batches.sort(key=lambda b: b.get("purchased_on", ""), reverse=True)
    from grocery import events
    all_events = events.all_events()
    batch_refs = {f"b{n}": b for n, b in enumerate(batches, start=1)}
    batch_lines = "\n".join(
        f"{ref} | {b['item']} | {b.get('product', '')} | {b.get('store', '')} | {b.get('purchased_on', '')} | "
        f"{b.get('units', '')} {b.get('unit', '')} | {events.fraction_text(events.wasted_share(b['batch_id'], all_events))}"
        for ref, b in batch_refs.items()
    ) or "(none)"

    history = records.purchase_history()
    history_refs = {f"h{n}": h for n, h in enumerate(history, start=1)}
    history_lines = "\n".join(
        f"{ref} | {h['item']} | {h.get('product', '')} | {h.get('store', '')} | {h.get('units', '')} "
        f"{h.get('unit', '')} | ${h.get('price_paid', '')} | {h.get('purchased_on', '')}"
        for ref, h in history_refs.items()
    ) or "(none)"

    product_refs = records.products_prompt_lines()
    now = timeutil.now()
    heard = "The user's voice recording is attached." if audio else f'The user typed: "{text}"'
    prompt = PROMPT.format(
        today=now.strftime("%Y-%m-%d"), weekday=now.strftime("%A"), heard=heard,
        batches=batch_lines, history=history_lines,
        items=", ".join(i["name"] for i in records.items()) or "(none yet)",
        products=products_text(product_refs),
        mode_rule=MODE_RULES[mode], units=UNITS, kinds=KINDS, storage=STORAGE, packs_rule=PACKS_RULE,
    )
    media = [(audio[0], audio[1])] if audio else []
    result = gemini.generate_json("voice", model, prompt, media)
    _attach_products(result.get("actions", []), product_refs)
    return result, batch_refs, history_refs


def model():
    return sheets.get_settings()["model_voice"]


WASTE_PROMPT = """A grocery tracker user is reporting that part of one batch went bad. Today is {today}.
Batch: {item} ({product}), bought {bought}: {units} {unit}.
They wrote: "{text}"
Work out the wasted share of the WHOLE batch as bought (e.g. "200 grams" of a 15 lb pack = 200/6804;
"one bell pepper" of 3 = 0.3333; "half" = 0.5). Convert units as needed.
"batch_done" is true unless they say some is still left or salvageable.
Return only JSON: {{"fraction": number between 0 and 1, "batch_done": true/false,
"happened_on": "YYYY-MM-DD" (today unless they say otherwise), "summary": a 3-8 word restatement}}"""


def parse_waste(batch, text):
    """Free-text 'some went bad' for one known batch → {"fraction", "batch_done", "happened_on", "summary"}."""
    prompt = WASTE_PROMPT.format(
        today=timeutil.today_str(), item=batch.get("item", ""), product=batch.get("product", ""),
        bought=batch.get("purchased_on", ""), units=batch.get("units", ""), unit=batch.get("unit", ""),
        text=text.replace('"', "'"),
    )
    return gemini.generate_json("waste_text", model(), prompt)


REFINE_PROMPT = """A grocery tracker user is reviewing a purchase before saving it, and is now adding details
they missed or correcting mistakes. Today is {today} ({weekday}).

{heard}

THE PURCHASE AS IT STANDS
Store: {store}   Date: {date}
Items (index | item | product | quantity | price):
{items}

RECENT PURCHASES (ref | item | product | store | quantity | price | bought on):
{history}

KNOWN PRODUCTS with the user's own nicknames (ref | item | product | one pack as sold | nicknames). When the
user uses a nickname or a close variant ("Costco egg whites", "wholesale mutton"), it means that product:
{products}

Apply only what the user says; anything about an item can change, not just price and weight.
{packs_rule}
If the store is unknown and they name a store brand, set the store too (Kirkland = Costco,
Great Value = Walmart, Signature Select = Safeway, H-E-B brand = H-E-B, Member's Mark = Sam's Club).
Examples: "I keep raspberries in the freezer" sets that item's storage to Freezer; "it's from Costco" changes the store; "I bought it 3 days ago"
changes the date; "the salmon was 2.4 pounds, 24 dollars" updates that item; "also a dozen eggs" adds an
item; "remove the bananas" removes one. Product names should carry every detail they give (brand, cut,
size). Leave anything they don't mention unchanged.

Return only JSON:
{{
  "transcript": what the user said, word for word,
  "store": new store or null, "date": new date "YYYY-MM-DD" or null,
  "updates": [{{"index": item index, and ONLY the fields the user changed, any of:
               "item", "product", "brand", "packs" (number), "units" (number), "unit" (one of {units}),
               "price" (dollars), "price_is_total" (true/false), "kind" (one of {kinds}), "storage" (one of {storage}),
               "perishable" (true/false), "expiry_date" ("YYYY-MM-DD")}}],
  "adds": [{{"item": generic item name, "product": full product description, "packs": number or null,
            "units": number or null, "unit": one of {units}, "price": dollars or null,
            "price_is_total": true/false, "based_on": purchase ref or null,
            "product_ref": known product ref or null, "said_name": the user's exact words for it,
            "kind": one of {kinds}, "storage": one of {storage}, "perishable": true/false,
            "emoji": one emoji or "", "packaged": true/false,
            "wiki": the English Wikipedia article title that best pictures this generic item (e.g. "Egg white",
              "Catla" for katla fish, "Ilish" for hilsa, "Half and half"), or "",}}],
  "removes": [item indexes]
}}"""


def refine(review, audio=None, text=None):
    """What the user adds or corrects on a review screen → store/date/updates/adds/removes."""
    history = records.purchase_history()
    history_refs = {f"h{n}": h for n, h in enumerate(history, start=1)}
    product_refs = records.products_prompt_lines()
    now = timeutil.now()
    prompt = REFINE_PROMPT.format(
        products=products_text(product_refs),
        today=now.strftime("%Y-%m-%d"), weekday=now.strftime("%A"),
        heard="The user's voice recording is attached." if audio else f'The user typed: "{text}"',
        store=review.get("store") or "(unknown)", date=review.get("date"),
        items="\n".join(f"{n} | {c['item']} | {c['product']} | {quantity_text(c)} | "
                        f"{'$%.2f' % c['price'] if c['price'] is not None else 'price unknown'}"
                        for n, c in enumerate(review["items"])) or "(none yet)",
        history="\n".join(
            f"{ref} | {h['item']} | {h.get('product', '')} | {h.get('store', '')} | {h.get('units', '')} "
            f"{h.get('unit', '')} | ${h.get('price_paid', '')} | {h.get('purchased_on', '')}"
            for ref, h in history_refs.items()) or "(none)",
        units=UNITS, kinds=KINDS, storage=STORAGE, packs_rule=PACKS_RULE,
    )
    result = gemini.generate_json("voice_refine", model(), prompt, [audio] if audio else [])
    _attach_products(result.get("adds", []), product_refs)
    return result, history_refs


def products_text(product_refs):
    return "\n".join(f"{ref} | {p.get('item', '')} | {p['name']} | {pack_text(p)} | {p.get('nicknames', '')}"
                     for ref, p in product_refs.items()) or "(none yet)"


def pack_text(product):
    size = records._float(product.get("pack_size"))
    return f"{size:g} {product.get('pack_unit', '')}".strip() if size else "pack size unknown"


def quantity_text(card):
    """How a review card's amount reads to the AI: "5 packs of 6 gal = 30 gal", or "2.4 lb"."""
    if card.get("packs") is not None:
        if card.get("pack_size"):
            return f"{card['packs']:g} packs of {card['pack_size']:g} {card['unit']} = {card['units']:g} {card['unit']}"
        return f"{card['packs']:g} packs (pack size unknown)"
    return f"{card['units']:g} {card['unit']}"


def _attach_products(actions, product_refs):
    """Swap a product ref for the real product row, so the purchase reuses that product."""
    for a in actions:
        p = product_refs.get(a.get("product_ref") or "")
        if p:
            a["product"], a["brand"], a["item"] = p["name"], p.get("brand", ""), p.get("item") or a.get("item")


NICKNAME_PROMPT = """Transcribe the attached recording. The user is saying one or more short names they use
for a grocery product (e.g. "Costco water", "Kirkland water"). Write each name exactly as spoken, word
for word. Do not guess, shorten, correct or invent names; if a part is unclear, leave it out.
Separate names where the user pauses or says "and" / "or".
Return only JSON: {{"heard": the full transcript, "nicknames": [each name as spoken]}}"""


def nicknames_from_audio(audio, product_name=""):
    result = gemini.generate_json("nickname", model(), NICKNAME_PROMPT, [audio])
    return [n for n in result.get("nicknames", []) if isinstance(n, str) and n.strip()]
