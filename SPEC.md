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

## More decisions (round 7)

- **Backdated events**: "avocados finished 3 days ago" → the end date is today − 3 days. This applies to any voice update.
- **Alcohol**: store **% ABV** (and volume) per product for beers and seltzers. Can be charted like nutrients.
- **Repeated identical lines across photos** (e.g. 6 separate water-case lines split across two photos): if the app suspects overlap, it **asks "How many did you buy?"** and the user enters the number.
- **Single eater**: only the user eats the food. Servings = the user's intake.
- **No freezing logic**: storage method doesn't change tracking. The user decides how to store things.
- **Uniform consumption**: assume each batch was consumed evenly from purchase to finish. Never ask how much is left.
- **Item granularity example**: whole milk ≠ half-and-half ≠ whipping cream. No need for 1%/2% splits.
- **Waste amounts are rough fractions** ("about a third of the carrots").
- **Phone-first** (Pixel), still usable on a computer.
- **Units**: USD, lb, gallons (US).

## Dashboard (home screen; user will detail later)

- On opening the app: what's in the **freezer, fridge, and pantry**, as a visual list, ideally with **small pictures** (onions, potatoes, sweet potatoes…) as a reminder to eat them.
- **"You probably forgot this" alerts**: if an open batch is much older than that item's usual lifespan (e.g. avocados usually last 2 weeks and it's been 4), remind the user something may be left at the back of the fridge.

## More decisions (round 8)

- **Storage place per item**: a dropdown with **Open air (pantry)**, **Fridge**, **Freezer**. The AI guesses the first time, the user confirms or changes it, and the app remembers.
- **Pictures**: real product photo if available, else food emoji. Final picture approach decided later.
- **Dashboard**: a must-have but **not a priority now**. Build it after the core works.
- **Forgot-it reminders**: for perishables, warn at about **1.25× the usual lifespan** (2 weeks → 2.5 weeks). **Non-perishables** (rice, detergent) never trigger reminders, however long they sit.
- **Voice preview**: before saving, show a small list of exactly what the voice step will do ("Close avocados batch from Oct 12", "Milk: 1/3 spoiled", "Butter: finished"). The user approves.
- **No old-receipt import**. Start fresh. A usual lifespan only exists after an item's first batch is finished. The app gets smarter over months and years.
- **Gemini paid Tier 1** key is available. No need to design around free-tier limits.
- **Stores = chain only**: Costco, Walmart, Safeway, Kroger, … plus "local grocery store". No branch locations.

### Why Gemini and not Claude (for the record)

- Gemini's API accepts **audio** directly. Claude's API takes text, images, and PDFs but **not audio**, so voice would need an extra speech-to-text service.
- The user already has a **paid Gemini key** and working Gemini code in Bookshelf Scanner.
- Claude reads receipt images well too, and could be added later as an alternative receipt reader if Gemini struggles.

### "Usual lifespan" definition (v2 proposal, awaiting approval)

- The "at least half eaten" cutoff was **rejected** by the user.
- New proposal is a **weighted average with no cutoff**. Each batch counts in proportion to how much was actually eaten:
  - `eaten share = 1 − wasted share`
  - `usual lifespan = Σ(eaten share × days open) ÷ Σ(eaten share)`
- A fully spoiled batch (eaten share 0) has **zero influence** and is recorded only as waste. A partly wasted batch counts partly.
- Only for **perishable** items.

### Expiry dates

- When known (voice "milk expires Nov 2", or read from a label photo), store the **expiry date** per batch.
- It's a hard upper limit for reminders. Many items (fresh produce) have none.

## Model arenas (future feature)

- The user wants in-app **arenas** to compare Gemini models (quality, tokens, cost, rate limits) per task.
- The app must let the **model be chosen per task** (receipt reading, voice parsing, label reading), not hard-coded. Same idea as `arena_benchmark.py` in Bookshelf Scanner.

## Stores (updated)

- Chains by name. Local stores **saved by their actual name** (user is in **Houston**; only a few local stores are visited repeatedly).

## Perishable flag (decided)

- The AI classifies each item as perishable or not, and the user can override it.

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
