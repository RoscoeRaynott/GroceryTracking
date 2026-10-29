"""Reading receipts and order screenshots with Gemini.

Two steps:
1. transcribe each photo on its own (saved as a draft right away);
2. merge the photos (dropping lines repeated where photos overlap), then
   interpret the merged lines into items in one call, using the existing
   item list so "an avocado is an avocado".
"""

import re

from grocery import gemini
from grocery.schema import KINDS, STORAGE, UNITS

TRANSCRIBE_PROMPT = """You are reading ONE photo of a grocery store receipt, or ONE screenshot of an
online grocery order (Walmart, Instacart, Costco, Safeway app, etc.). It may be one of several
overlapping photos of the same long receipt, so transcribe exactly what is visible in THIS photo.

List every line top to bottom, including partly cut-off lines at the top or bottom edge.
Return only JSON in this shape:
{
  "source_type": "printed_receipt" | "app_screenshot",
  "store": store name if visible (e.g. "Costco", "Walmart"), else null,
  "date": "YYYY-MM-DD" if visible, else null,
  "time": "HH:MM" (24h) if visible, else null,
  "shows_header": true if the store name / top of the receipt is visible,
  "shows_total": true if the final total is visible,
  "total_paid": final amount paid if visible, else null,
  "tax": total tax if visible, else null,
  "lines": [
    {
      "text": the line exactly as printed (or the product title in a screenshot),
      "kind": "item" | "discount" | "tax" | "fee" | "subtotal" | "total" | "payment" | "other",
      "quantity": number of units if shown (e.g. "2 @ 3.49" -> 2, "2.31 lb @ 1.29/lb" -> 2.31), else null,
      "quantity_unit": "each" | "lb" | "oz" | null,
      "amount": the line's dollar amount as a number (discounts and refunds negative), else null,
      "unclear": true if you cannot read this line with confidence
    }
  ]
}
Rules: never invent lines or prices. Bag fees, bottle deposits, memberships and gift cards are
"fee". Instant savings / coupons / markdowns are "discount". If a price is unreadable, use null
and mark the line unclear."""

INTERPRET_PROMPT = """You turn transcribed grocery receipt lines into purchased items for a personal
grocery tracker in Houston, Texas, USA. Store: {store}. Source: {source_type}.

The user's EXISTING items are below. A generic item ignores brand and store: an avocado is an
avocado, milk from Costco and Walmart is the same item. If a line is one of these, use the
existing name EXACTLY and set "is_new": false. Truly different foods stay separate
(duck eggs != chicken eggs; whole milk != half-and-half != whipping cream).
EXISTING ITEMS (name | kind | unit | storage | perishable):
{items}

RECEIPT LINES (index: kind | text | quantity | amount):
{lines}

Return only JSON:
{{
  "items": [
    {{
      "lines": [indexes of the receipt lines this item uses, including its discount lines],
      "item": generic item name, short and singular (e.g. "Avocado", "Whole milk", "Laundry detergent – pods"),
      "is_new": true if not in the existing items,
      "kind": one of {kinds} ("Food"; "Household" for consumables like trash bags, detergent,
              shampoo, paper towels; "One-time" for durable things like a chair or monitor),
      "unit": one of {units}: the standard unit to count this item in (each for countable things,
              lb for meat/produce sold by weight, gal for milk, loads for detergent),
      "storage": one of {storage} (where it is usually kept; "Open air" means pantry/counter),
      "perishable": true or false,
      "emoji": one food emoji for the item, or "" if none fits,
      "product": product name as sold, expanded from receipt abbreviations
                 (e.g. "KS ORG EGGS 24CT" -> "Kirkland Signature Organic Eggs, 24 ct"),
      "brand": brand if known, else "",
      "units": total quantity bought in the item's unit (e.g. 2 x 1-gallon jugs -> 2; a 6-ct bag
               of avocados -> 6; 2.31 lb of carrots -> 2.31; 104-load pods -> 104),
      "price": final price paid for this item in dollars AFTER its discounts, BEFORE tax,
      "unclear": true if the item, quantity or price is a guess,
      "note": short reason when unclear, else ""
    }}
  ],
  "skipped": [
    {{"lines": [indexes], "reason": "Bag fee" | "Membership" | "Gift card" | "Deposit" | short reason}}
  ]
}}
Every "item" and "fee" line must appear in exactly one item or skipped entry. Ignore subtotal,
tax, total and payment lines. Never invent prices; if an item's price is unknown use null and
mark it unclear."""


def transcribe(model, jpeg):
    return gemini.generate_json("receipt_read", model, TRANSCRIBE_PROMPT, [(jpeg, "image/jpeg")])


def order_photos(readings):
    """Put the photo showing the store header first and the one showing the total last."""
    def rank(r):
        return (0 if r.get("shows_header") else 1) + (1 if r.get("shows_total") else 0)
    return sorted(readings, key=rank)  # stable: ties keep the order photos were added


def _key(line):
    text = re.sub(r"[^a-z0-9]", "", str(line.get("text", "")).lower())
    amount = line.get("amount")
    return text, round(float(amount), 2) if isinstance(amount, (int, float)) else None


def merge(readings):
    """Join photos in order, removing lines repeated where two photos overlap.

    Returns (merged lines, questions). A question is raised when the overlap
    contains an item line that also repeats next to it (e.g. six identical
    water cases), because then we can't tell overlap from real repeats.
    """
    merged, questions = [], []
    for photo_no, reading in enumerate(readings, start=1):
        lines = [dict(l, photo=photo_no) for l in reading.get("lines", [])]
        if not merged:
            merged = lines
            continue
        overlap = 0
        for k in range(min(len(merged), len(lines)), 0, -1):
            if [_key(l) for l in merged[-k:]] == [_key(l) for l in lines[:k]]:
                overlap = k
                break
        boundary = len(merged) - overlap
        merged = merged + lines[overlap:]
        if overlap:
            seen = set()
            for line in merged[boundary:boundary + overlap]:
                key = _key(line)
                if line.get("kind") != "item" or key in seen:
                    continue
                seen.add(key)
                run = [i for i, l in enumerate(merged) if _key(l) == key]
                if len(run) > 1 or _repeats_nearby(lines, key):
                    questions.append({"key": key, "text": line.get("text", ""), "count": len(run)})
    return merged, questions


def _repeats_nearby(lines, key):
    return sum(1 for l in lines if _key(l) == key) > 1


def apply_answers(merged, questions, answers):
    """Make the number of identical lines match what the user says they bought."""
    for q in questions:
        want = int(answers.get(q["text"], q["count"]))
        idx = [i for i, l in enumerate(merged) if _key(l) == q["key"]]
        if not idx or want == len(idx):
            continue
        if want > len(idx):
            merged[idx[-1] + 1:idx[-1] + 1] = [dict(merged[idx[-1]]) for _ in range(want - len(idx))]
        else:
            for i in reversed(idx[want:]):
                del merged[i]
    return merged


def interpret(model, store, source_type, merged, items):
    item_lines = "\n".join(
        f"{i['name']} | {i['kind']} | {i['unit']} | {i['storage']} | {i['perishable']}" for i in items
    ) or "(none yet)"
    line_text = "\n".join(
        f"{n}: {l.get('kind')} | {l.get('text')} | {l.get('quantity')} {l.get('quantity_unit') or ''} | {l.get('amount')}"
        for n, l in enumerate(merged)
    )
    prompt = INTERPRET_PROMPT.format(
        store=store or "unknown", source_type=source_type or "unknown", items=item_lines,
        lines=line_text, kinds=KINDS, units=UNITS, storage=STORAGE,
    )
    return gemini.generate_json("receipt_items", model, prompt)


def summarize(readings):
    """Store, date, time, totals from the photos (first value found wins)."""
    out = {"store": None, "date": None, "time": None, "total_paid": None, "tax": None, "source_type": None}
    for r in readings:
        for k in out:
            if out[k] in (None, "") and r.get(k) not in (None, ""):
                out[k] = r.get(k)
    return out
