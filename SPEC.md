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

## Feasibility notes (agreed facts)

- Product barcodes (UPC/EAN) can be scanned with a phone camera and looked up in product databases, such as Open Food Facts (free).
- Store brands are only partly covered. Produce and deli items often have no standard barcode.
- A receipt barcode only holds a transaction ID, so it can't list the items.
- Alternative for receipts: **photo of the receipt → read the text (OCR/AI)**, or use the store's digital receipts.

## Open questions

- (pending)
