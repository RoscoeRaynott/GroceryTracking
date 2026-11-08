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
      "unclear": true if you cannot read this line with confidence,
      "picture_box": in an app screenshot, the product's own picture shown beside this item as
                     [ymin, xmin, ymax, xmax] scaled 0-1000 of this image; null if none (always null on paper receipts)
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

The user's KNOWN PRODUCTS (ref | item | product | one pack as sold | nicknames). If a line is one of
these exact products (same brand and product, even if the receipt abbreviates it or writes the size
differently, e.g. "KS WATER 40PK" = "Kirkland Signature Purified Drinking Water, 40 x 16.9 fl oz"),
set "product_ref" to its ref and use its item. Different brands or variants are NOT the same product.
{products}

{mixed_rule}RECEIPT LINES (index: kind | text | quantity | amount):
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
              lb for meat/produce sold by weight, gal for milk and drinking water,
              ml for other liquids like egg whites or juice, loads for detergent),
      "storage": one of {storage} (where it is usually kept; "Open air" means pantry/counter),
      "perishable": true or false,
      "alcohol": true if it is an alcoholic drink (beer, wine, hard seltzer, spirits, sake, hard cider…), else false,
      "emoji": one food emoji for the item, or "" if none fits,
      "wiki": the English Wikipedia article title that best pictures this generic item (e.g. "Egg white",
              "Catla" for katla fish, "Ilish" for hilsa, "Half and half"), or "",
      "product": product name as sold, expanded from receipt abbreviations
                 (e.g. "KS ORG EGGS 24CT" -> "Kirkland Signature Organic Eggs, 24 ct"),
      "brand": brand if known, else "",
      "packaged": true if sold in a labelled package (has an ingredients list / nutrition label),
                  false for loose produce, deli, bakery or anything without a label,
      "product_ref": ref of the KNOWN PRODUCT this line is, or null,
      "packs": number of packs as sold on this line for a packaged product (e.g. "2 @ 4.99" -> 2,
               a single line -> 1), or null for loose / weighed things,
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


def transcribe(model, jpeg, on_text=None, thinking=None, task="receipt_read"):
    extra = {"thinking": thinking} if thinking else {}  # the normal path sends exactly what it always did
    return gemini.generate_json(task, model, TRANSCRIBE_PROMPT, [(jpeg, "image/jpeg")], on_text=on_text, **extra)


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
        lines = [dict(l, photo=photo_no, src=reading.get("source_type") or "") for l in reading.get("lines", [])]
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


MIXED_RULE = """IMPORTANT: these lines come from TWO views of the SAME purchase: the printed receipt [paper] and
screenshots of the same order in the store's app [app], which show more detail (full names, sizes, pictures).
Every thing bought appears in BOTH. Make exactly ONE item per thing bought and put ALL of its lines (the paper
line and the app line, plus discounts) in its "lines". Use the app's full product name and size; the price is
the same thing paid once (if the two disagree, use the paper receipt's and mark it unclear). Never count a
thing twice. A line found in only one view is still one item.
"""


def interpret(model, store, source_type, merged, items, product_refs=None, on_text=None, thinking=None,
              task="receipt_items"):
    """Receipt lines -> items. product_refs = {ref: product row} the AI may match lines to;
    on_text(reply so far) is called while the reply streams in (for live progress)."""
    item_lines = "\n".join(
        f"{i['name']} | {i['kind']} | {i['unit']} | {i['storage']} | {i['perishable']}" for i in items
    ) or "(none yet)"
    mixed = len({l.get("src") for l in merged if l.get("src")}) > 1  # a paper receipt plus app screenshots
    tag = {"printed_receipt": "[paper] ", "app_screenshot": "[app] "}
    line_text = "\n".join(
        f"{n}: {tag.get(l.get('src'), '') if mixed else ''}{l.get('kind')} | {l.get('text')} | "
        f"{l.get('quantity')} {l.get('quantity_unit') or ''} | {l.get('amount')}"
        for n, l in enumerate(merged)
    )
    from grocery import voice  # local import: voice imports records, which imports this module's users
    prompt = INTERPRET_PROMPT.format(
        store=store or "unknown", source_type=source_type or "unknown", items=item_lines,
        products=voice.products_text(product_refs or {}),
        lines=line_text, kinds=KINDS, units=UNITS, storage=STORAGE, mixed_rule=MIXED_RULE if mixed else "",
    )
    extra = {"thinking": thinking} if thinking else {}
    return gemini.generate_json(task, model, prompt, on_text=on_text, **extra)


def summarize(readings):
    """Store, date, time, totals from the photos (first value found wins)."""
    out = {"store": None, "date": None, "time": None, "total_paid": None, "tax": None, "source_type": None}
    for r in readings:
        for k in out:
            if out[k] in (None, "") and r.get(k) not in (None, ""):
                out[k] = r.get(k)
    return out


def box_of(line):
    """The picture box a line came with, as [ymin, xmin, ymax, xmax] in 0-1000, whatever shape the AI used
    (a list, {"ymin":…}, "box_2d"), or None."""
    box = line.get("picture_box") or line.get("box_2d") or line.get("box")
    if isinstance(box, dict):
        box = [box.get(k) for k in ("ymin", "xmin", "ymax", "xmax")]
    if isinstance(box, (list, tuple)) and len(box) == 4:
        try:
            return [float(v) for v in box]
        except (TypeError, ValueError):
            return None
    return None


def add_pictures(reading, jpeg, size=140):
    """Cut each item's picture out of an order screenshot right after it's read, and keep it on the line
    ("picture"), so it survives everything that happens to the lines afterwards. Returns how many."""
    n = 0
    for line in reading.get("lines", []):
        box = box_of(line)
        if box and line.get("kind", "item") == "item":
            pic = cut_picture(jpeg, box, size)
            if pic:
                line["picture"] = pic
                n += 1
    return n


def match_picture(product, amount, lines, used):
    """The picture of the screenshot line that is this item, when the AI didn't link that line to it:
    the same price and most words in common with the product name."""
    words = set(re.findall(r"[a-z0-9]+", str(product).lower())) - {"oz", "ct", "lb", "fl", "pack", "the", "and", "of"}
    best, best_score = None, 0
    for line in lines:
        pic = line.get("picture")
        if not pic or pic in used:
            continue
        if amount is not None and isinstance(line.get("amount"), (int, float)) and abs(line["amount"] - amount) > 0.01:
            continue
        common = words & set(re.findall(r"[a-z0-9]+", str(line.get("text", "")).lower()))
        score = len(common) / max(1, len(words))
        if len(common) >= 2 and score > best_score:
            best, best_score = pic, score
    return best if best_score >= 0.5 else ""


def cut_picture(jpeg, box, size=200):
    """The product picture an online order shows beside an item, cut out of the screenshot as a small copy
    (a data URI). box = [ymin, xmin, ymax, xmax] scaled 0-1000. "" if the box doesn't look like a picture."""
    import io
    from PIL import Image
    from grocery import productinfo
    try:
        y0, x0, y1, x1 = (float(v) for v in box)
        img = Image.open(io.BytesIO(jpeg)).convert("RGB")
        w, h = img.size
        if max(y0, x0, y1, x1) > 1000:  # given in pixels instead of 0-1000
            y0, y1, x0, x1 = y0 / h * 1000, y1 / h * 1000, x0 / w * 1000, x1 / w * 1000
        left, top, right, bottom = x0 / 1000 * w, y0 / 1000 * h, x1 / 1000 * w, y1 / 1000 * h
        bw, bh = right - left, bottom - top
        if bw < 24 or bh < 24 or bw > 0.6 * w or not 0.33 < bw / bh < 3:
            return ""  # too small, too wide or a strip of text: not a product picture
        crop = img.crop((max(0, left), max(0, top), min(w, right), min(h, bottom)))
        out = io.BytesIO()
        crop.save(out, format="JPEG", quality=90)
        return productinfo.thumbnail(out.getvalue(), size)
    except (TypeError, ValueError, OSError):
        return ""
