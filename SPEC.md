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
- **Decided:** build option 1 (in-app voice) first. Hey Google and Alexa are **future work**.

## AI provider (decided: Gemini)

- **Gemini API** with a key stored as a Streamlit secret.
- Gemini accepts **images** (receipt photos) and **audio** (voice notes) directly, so one provider covers both.
- The key comes from **Google AI Studio**, which is separate from Antigravity and Google consumer subscriptions.
- A Claude Pro chat subscription **doesn't include API access**. Claude's API is billed separately.
- The user can also create Claude API keys (platform.claude.com, pay-per-use) if ever needed.

## Storage (decided: Google Sheets)

- Data is stored in a **Google Sheet**, so the user can read and fix it directly.
- The user has done this before in their **Bookshelf Scanner** repo (Sheets access via credentials in Streamlit secrets).
- **Reuse the Bookshelf Scanner pattern exactly:**
  - Libraries: `gspread` + `google-auth`, using a service account.
  - Secrets: `[gcp_service_account]` (the service account JSON), `[gsheets] spreadsheet_url`, and `GEMINI_API_KEY`.
  - Gemini is called directly over its REST API (`generativelanguage.googleapis.com`, `x-goog-api-key` header) with JSON output.

## Two levels of "item" (needed for ingredients)

- **Generic item**: Avocado, Sausage, Milk. Used for lifespan, cost/week, and waste.
- **Specific product**: "Kirkland Italian Chicken Sausage" (barcode, brand). Used for ingredients and nutrition.
- Each specific product belongs to one generic item. Fresh produce usually has no specific product.

## Ingredient & nutrition tracking (new, under discussion)

- For each specific product, store **ingredients, nutrition facts, additives/flags** (e.g. HFCS, artificial colors, palm oil).
- Sources, in order:
  1. **Open Food Facts** by barcode. It's free and includes ingredients, nutrition, additives, and a palm-oil flag, but coverage varies.
  2. **Photo of the ingredient/nutrition label → Gemini extracts it.** Reliable fallback.
- A receipt alone doesn't give ingredients. Each receipt line must be matched to a known product, or the label must be photographed once.
- Goal: **charts over time** of how much of each ingredient/flag the user consumed, e.g. per month.
- Limits to be honest about:
  - Labels list ingredients but rarely their **amounts**.
  - Nutrition facts **do** give amounts (sugar, sodium, etc.).
  - GMO status is only known if the package says so (e.g. "Non-GMO", "bioengineered").

## Quick add by voice (decided)

- With no receipt or scan, the user can say: "6 avocados from Costco, the 2-gallon milk set from Costco, one big salmon fillet from Costco."
- The app matches each item to **past purchases** at that store, copies the product details and last price, and shows them to confirm or edit.

### Ingredient counting method (decided)

- Count **servings**, not grams of an ingredient, because labels don't give ingredient amounts.
- Example: a 16-serving sausage pack containing HFCS, finished → **16 servings of HFCS**. Plus 4 Cokes with HFCS → **20 HFCS servings** that month.
- Servings eaten = total servings in package − wasted portion.
- Nutrition facts (sugar g, sodium mg, …) can be summed the same way, per serving × servings eaten.
- The app **remembers** each product's ingredients and only asks for a label photo the first time a product is unknown.
- Chart design and which flags to highlight: **discuss later**. The user will choose filters.

### Splitting across months (decided)

- Servings are split by days. A batch from Jan 1 to Feb 20 (51 days) with 16 servings → January gets 16 × 31/51 ≈ 9.7, February gets 16 × 20/51 ≈ 6.3.

### Two kinds of ingredient data (decided)

- **Flag ingredients** (HFCS, palm oil, artificial/natural colors…): labels give no amount → counted as **servings containing it**.
- **Measured nutrients** (sodium, potassium, cholesterol, trans fat, total fat, sugar, carbs, protein…): labels give **amount and %DV per serving** → summed as real amounts and **%DV per day / per month**. This is the clearer, primary chart.

## What gets tracked (decided)

- **Everything on the receipt is listed**, not just food. The app classifies each line intelligently:
  1. **Food**: lifespan + cost + waste + ingredients + nutrition.
  2. **Consumable non-food** (trash bags, paper towels, detergent, shampoo, hair oil): lifespan + cost + waste. **No ingredients.**
  3. **One-time / durable** (chair, monitor, keyboard, dustbin): recorded, but **not tracked** for lifespan.
- The user can change any item's category or turn tracking off.
- Not expected on receipts: Costco gas.

## Receipt reading (decided)

- **Several photos per receipt** (long Costco receipts). The app **removes items that appear in two overlapping photos**.
  - Risk: two genuinely identical items in a row could look like overlap → flag these for the user to decide.
- **Unclear lines are flagged**: "This price/item was unreadable. What was it?"
- **Price = what was actually paid for the item, after discounts/coupons, excluding tax.** Costco-style discount lines must be applied to the item above them.
- **Sources:**
  - **Printed receipt photos**: abbreviated names, prices, discount lines.
  - **Online order screenshots** (Walmart, Instacart, Costco apps): clear names, quantities, prices.
- The AI **detects which kind it is** and reads it accordingly.

## Review screen (decided: must be excellent on mobile)

- The user always reviews items and prices before saving.
- **Must be smooth on a phone.** No cramped, side-scrolling tables (a known Streamlit pain point on mobile).
- Planned approach: **one card per item** (name, qty, price, category, flags), with problem items shown first, big tap targets, and edit in place.

## Barcode scanning (decided)

- **v1: photo of the barcode** (`st.camera_input` → decode with a barcode library).
- **Future: live scanning** (beeps when it finds a barcode). The user has a Pixel phone. Photo upload was painful in Bookshelf Scanner, so camera integration needs care.

## Users (decided: single user for now)

- **One user, no login** for version 1.
- Future: multiple people (friends), each with their own login and their own sheet. Out of scope until v1 works.

## Batch lifecycle (decided)

- Every purchase creates a **batch** (e.g. "Avocado ×6, Costco, Oct 29, $6.50"), which stays **open** until explicitly closed.
- The user closes batches by **voice**: "butter is finished", "eggs finished today", "2 avocados rotted".
- Restocking doesn't close the old batch automatically. When the user **adds a purchase of an item that has an open batch, the app asks "Is your last batch of X finished?"** and the user answers yes or no.
- **Lifespan = purchase date → finished date**, not purchase → next purchase. The user often waits days before rebuying, and that gap shouldn't count.

## Feasibility notes (agreed facts)

- Product barcodes (UPC/EAN) can be scanned with a phone camera and looked up in product databases, such as Open Food Facts (free).
- Store brands are only partly covered. Produce and deli items often have no standard barcode.
- A receipt barcode only holds a transaction ID, so it can't list the items.
- Alternative for receipts: **photo of the receipt → read the text (OCR/AI)**, or use the store's digital receipts.

## Open questions

- (pending)
