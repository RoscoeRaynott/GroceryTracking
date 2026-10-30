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

Work out every action the user wants. Possible actions:
- "waste": part or all of a batch went bad / was thrown away. "fraction" = share of the WHOLE batch
  as bought (2 avocados of a 6-pack = 0.3333; "half the spinach" = 0.5; "a third" = 0.3333).
  Reporting waste normally means the batch is done: the wasted part went bad and the rest was eaten,
  so set "batch_done": true. Set it to false ONLY if the user says some is still left to eat
  (e.g. "one avocado went bad, I still have the others").
- "finish": a batch is used up. "rest_wasted": true if they say the remainder was thrown away.
- "expiry": the user says when a batch expires. "expiry_date" as YYYY-MM-DD.
- "delete": the user wants a batch removed (e.g. "delete the avocados I added yesterday").
- "add": the user bought something. Use RECENT PURCHASES to fill in product, quantity and price when
  they refer to a usual purchase ("the 2-gallon milk from Costco"); "based_on" = that ref.
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
      "units": number, "unit": one of {units},
      "price": dollars or null if unknown, "based_on": purchase ref or null, "purchased_on": "YYYY-MM-DD",
      "kind": one of {kinds}, "storage": one of {storage}, "perishable": true/false, "emoji": one emoji or "",
      "packaged": true/false, "said": short quote}}
  ],
  "questions": [
    {{"text": a short question for the user, "options": [{{"label": short answer, "batch": ref or null}}],
      "action": the action this answer completes, with "batch" left null}}
  ],
  "not_understood": anything you could not map to an action, or ""
}}"""

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

    now = timeutil.now()
    heard = "The user's voice recording is attached." if audio else f'The user typed: "{text}"'
    prompt = PROMPT.format(
        today=now.strftime("%Y-%m-%d"), weekday=now.strftime("%A"), heard=heard,
        batches=batch_lines, history=history_lines,
        items=", ".join(i["name"] for i in records.items()) or "(none yet)",
        mode_rule=MODE_RULES[mode], units=UNITS, kinds=KINDS, storage=STORAGE,
    )
    media = [(audio[0], audio[1])] if audio else []
    result = gemini.generate_json("voice", model, prompt, media)
    return result, batch_refs, history_refs


def model():
    return sheets.get_settings()["model_voice"]
