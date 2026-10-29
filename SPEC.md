# GroceryTracking — Working Spec

_Status: gathering requirements. No code until the plan is approved._

## What the user has described

- Groceries come from **Walmart, Costco, Safeway, or local grocery stores**.
- Common thread: **every food item has a barcode**. Costco receipts also carry a barcode.
- **Task 1:** identify products from barcodes.
  - Preferred: scan the **receipt's barcode** to get all items at once.
  - Fallback: scan **individual products** one by one and identify each.

## Decisions

- **Two input methods, user picks either one:**
  1. **Receipt photo**: the app reads the whole receipt (store, date, time, items, prices).
  2. **Individual product scan**: barcode lookup, one item at a time.

## Core goal: learn how long things last and what is truly cheaper

- For every purchase, record **what was bought, from which store, date/time, quantity, and price**.
- When the **same item is bought again**, calculate **how long the previous batch lasted**.
- Compare stores and pack sizes by real value, not just sticker price.
  - Example: 3 avocados from Walmart lasted 1 week. 6 from Costco cost slightly more but lasted 3 weeks. Costco was the better buy.
- Track **waste**: the user will mark when part of a batch spoiled (input method TBD). Bulk buying is worth it for some items (long shelf life) but not for others (they rot).
- Keep history **over a year and across years**: average lifespan per item, average cost, and the best store and pack size.
- What the user does with these insights: **TBD by the user later**.

## Item identity (decided)

- Items are **generic products, not brands**: Avocado, Chicken eggs, Duck eggs, Milk, Spinach, Carrots.
- Store and brand don't matter for lifespan tracking. Walmart and Costco avocados are both "Avocado".
- Truly different foods stay separate: duck eggs ≠ chicken eggs.

## Logging waste / "finished" (decided: voice first)

- Preferred: **voice commands**, e.g. "half the spinach I last bought, two avocados, and a third of the carrots rotted."
- The app finds the **most recent batch** of each item and records the wasted portion and how long it lasted.
- Insights like "you may be buying too many carrots" come from the waste history.
- Fallback: manual entry in the app.

### Voice options

1. **In-app voice button (Streamlit), the primary option.** Record audio → speech-to-text → AI parses into structured updates → user confirms → saved. Uses only standard, existing tools.
2. **"Hey Google" hands-free, unverified.** Gemini doesn't offer a confirmed way to send speech to a custom app. The known route is IFTTT (Google Assistant trigger → webhook → our database). Two things are unconfirmed: whether it captures free-form sentences, and whether it needs a paid IFTTT plan. Streamlit can't receive webhooks, so this also needs a small separate endpoint or a database with a REST API. **Needs a hands-on test before committing.**
3. **Alexa, possible.** Custom skills with an HTTPS backend appear to still work. Same backend requirement as option 2.

- Platform assumption: **Streamlit app**, from the connected GitHub repo.

## Feasibility notes (agreed facts)

- Product barcodes (UPC/EAN) can be scanned with a phone camera and looked up in product databases, such as Open Food Facts (free).
- Store brands are only partly covered. Produce and deli items often have no standard barcode.
- A receipt barcode only holds a transaction ID, so it can't list the items.
- Alternative for receipts: **photo of the receipt → read the text (OCR/AI)**, or use the store's digital receipts.

## Open questions

- (pending)
