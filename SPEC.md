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
- **Google's read limit (~60 reads/minute) and how the app stays under it:**
  - The app keeps an in-memory copy of each tab. Opening the app reads each tab once; every save updates both the Sheet and the copy, so normal use needs no further reads.
  - The copy is re-read from Google every 10 minutes, so edits made by hand in the Sheet show up within 10 minutes.
  - Deleting rows always reads the tab fresh first, because deletes go by row number.
  - If Google still answers "too many requests", the app waits (5 s, 15 s, 30 s) and retries instead of failing.

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

- **Accepted as a starting point.** The user may change the formula later (e.g. after 6 months).

### Recompute rule (decided, architectural)

- The Sheet stores only **raw facts**: purchases, batches, waste/finish events, product details.
- Every derived number (usual lifespan, cost per unit, nutrient totals) is **calculated from raw facts**, never typed in by hand.
- Changing a formula → **all history is recalculated** automatically.

### Per-unit standardization (decided)

- Each item has a **standard unit**: count (avocado, apple, egg, trash bag), volume (gallon, fl oz), or weight (lb, oz).
- Pack sizes are converted to that unit: ½-gallon jar = 0.5 gal, 10 apples = 10.
- Lifespan is tracked **per unit**: 3 avocados lasting 8 days → 8/3 ≈ 2.7 days per avocado.
- Updated formula (v3, per unit):
  - `units eaten = units bought × (1 − wasted share)`
  - `usual days per unit = Σ(eaten share × days) ÷ Σ(units eaten)`
  - `expected lifespan of a new batch = units bought × usual days per unit`

### Non-perishables (decided)

- **No warnings**, but lifespan **is tracked** (detergent, trash bags, dish soap) to compare small vs large packs.

### Which history counts (decided direction)

- Some items vary a lot. Salmon can be eaten in 1 week, or sit 2 weeks in the freezer and take 2 more. The user accepts that **high variation is normal**.
- Use a **running average of the last 5–10 batches**, not a time window. Exact N is tunable later.
- **Warnings only start after enough finished batches** for that item (minimum count pending).

### Per-item lifespan chart (decided, on-demand tab)

- For each item, a **bar chart, one bar per batch, in purchase order**. There's no calendar axis, because lifespan variation is the point.
  - Bar height = days the batch lasted.
  - The **wasted portion is shaded** (e.g. ⅔ of the bar shaded if ⅔ was wasted).
  - Optional overlay: a running-average line (last 5–10 batches).
- More statistics: future work.

### Overlapping batches (decided: non-issue)

- The user never eats from old and new batches at the same time. Starting a new batch means the old one is finished, and the user will say so.

### "Best place to buy" (decided)

- For each item, the app shows a plain-language verdict: "**Avocado → Costco is best**", "**Mutton → [brand] at [store] is best**".
- It must say "**Not enough data yet**" until it can actually tell the options apart (threshold pending).
- **Tap the verdict → explanation**: price per unit eaten at each store, how much was wasted, and why the winner won. The "real cost per eaten unit" math lives here, not on the main screen.

- **Enough data** = at least **2 finished batches from each of at least 2 options**.
- **Compared options = store + brand + pack size** (e.g. "Kirkland 6-pack at Costco" vs "loose at Walmart").

### Google Sheet (decided)

- A **new, separate Sheet**, using the same service-account approach as Bookshelf Scanner.

### Warnings (decided)

- **At least 3 finished batches** of an item before lifespan warnings start.

### Same item on one receipt (decided)

- 3 gallons of milk on one receipt = **one batch of 3 gallons**. All math is per standard unit (per gallon).

### Expiry dates (input, decided)

- Captured **by voice** ("the milk expires Nov 2") or read from a label photo. **No manual expiry field is needed.** Item cards stay editable in case of a mistake.

### Detergents (decided)

- **Pods and liquid are separate items** (e.g. "Laundry detergent – pods", "Laundry detergent – liquid").
- Common comparison metric: **loads**. Both pod packs and liquid bottles state "X loads" on US labels. This gives **cost per load** and **days per load** across both. It's the manufacturer's claim, so actual use may differ.

### Expiry dates

- When known (voice "milk expires Nov 2", or read from a label photo), store the **expiry date** per batch.
- It's a hard upper limit for reminders. Many items (fresh produce) have none.
- **Warn 2 days before** a known expiry date.

## Model arenas (future feature)

- The user wants in-app **arenas** to compare Gemini models (quality, tokens, cost, rate limits) per task.
- The app must let the **model be chosen per task** (receipt reading, voice parsing, label reading), not hard-coded. Same idea as `arena_benchmark.py` in Bookshelf Scanner.

## Stores (updated)

- Chains by name. Local stores **saved by their actual name** (user is in **Houston**; only a few local stores are visited repeatedly).

## Perishable flag (decided)

- The AI classifies each item as perishable or not, and the user can override it.

## Interface (decided)

- **Home screen with 4 big buttons**: Add purchase, Voice update, Pantry, Insights. Settings is a small link. Reminders show at the top of Home.
- No tab row and no sidebar. Each screen has "← Home".
- **Theme follows the phone** (light/dark).
- A **clickable mockup** is built and approved before real UI code.

## Models (starting choice; changeable in Settings)

- Receipts: Gemini 3.8 Flash (upgrade: Gemini 4 Argon, which is available on the user's key).
- Voice: Gemini 3.8 Flash. Labels: Gemini 3.5 Flash Lite.
- Rate limits are a non-issue at this app's volume (~50 calls/day max). Every call is logged in `ai_log`.

## Phase 1 decisions

- **Photo upload**: reuse Bookshelf Scanner's lessons:
  - A browser-side uploader that shrinks photos on the phone before sending, with multiple photos at once.
  - A standard uploader as fallback for HEIC (Pixel "High efficiency" photos), plus `pillow-heif`.
  - The photo queue is kept in session.
  - Known Bookshelf problems: HEIC silently failing, huge files dropping the connection, and Android reloading the page when switching to the gallery/camera.
- **"Is your last X finished?" → Yes**: the finish date **defaults to today**, editable on the card. A date said later by voice ("finished 3 days ago") overrides it.
- **Receipt photos are not stored anywhere.** Only the AI's text reading is saved.
- **Refresh protection: both.**
  - Photos are kept in session memory the moment they arrive (Bookshelf approach).
  - Each photo's AI reading is saved as a **draft** in the Sheet, so a full reload can resume.
  - **Drafts are deleted once the receipt is saved or discarded.**
- **The Sheet must stay clean and human-readable**: no leftover rough work in the main tabs.
  - Raw AI readings go in their own **ai_readings** tab, not in purchases.
  - Rows that link by ID also show **plain names** (item, store, date) next to the IDs.
- **Non-product lines** (bag fees, memberships, gift cards, deposits) are skipped automatically and listed as "Skipped" at the bottom of Review, with "Add back".
- **New items**: the AI suggests name, kind, unit, storage, and perishable. The card is marked **New** with those fields open. Later purchases match automatically.

## Phase 2 decisions (barcodes & product info)

- **Single-item barcode scan**: ask the **price**. Store and date default to the entry day (date = today). Receipt scans don't need this.
- **Ingredients only for things you eat or drink.** No ingredient info for shampoo, detergent, sprays, etc.
- **Where food product info comes from**, in order (proposal pending confirmation):
  1. Open Food Facts by barcode.
  2. Open Food Facts by name, **only if the brand matches exactly**.
  3. **Gemini with Google Search grounding**: web search for that exact brand + product, returning its sources. Shown as "possible match" for the user to confirm.
  4. **Label photo (ingredients + nutrition facts)**: always the truth, overrides everything.
- **USDA FoodData Central** for fresh produce nutrition: free API key, signed up later (Phase 5).
- **Testing vs real receipts**: the user decides. Old receipts can be real (scanned a day or two late).
  - Every Review screen ends with **"Save" or "Discard, don't save"**. Discard leaves the Sheet untouched.
- **Needs info**: a small link on Home ("N food products need info") opens the list. Each product can be filled by label photo, barcode, or web search (confirm first), or marked "no label".
- **Every decision so far is provisional** until the user has tested with real receipts. Expect changes after real use.

## Phase 3 decisions (voice)

- **One shared voice engine** behind both "Say it" (Add purchase) and "Voice update", ready to merge later.
- **Ambiguous batch** ("the milk"): use the **most recent** open batch. The preview names the batch so a wrong pick is caught.
- **Follow-up questions** are on-screen text with tap options ("Which milk? [Oct 14] [Oct 28]"). Spoken replies are **future work**.
- **Deleting by voice is allowed**, always previewed and undoable.
- **Reporting waste closes the batch** (the wasted part went bad, the rest was eaten), unless the user says some is still left ("one avocado went bad, I still have the others").
- **"Missed something?" mic on every review screen**: after the AI's first pass, speak or type what was missed or wrong (store, date, weight, price, another item, remove one). The cards update in place before saving.
- **Voice fixes can change any field** of a card: item, product, brand, quantity, unit, price, kind, kept in (storage), perishable, expiry date, plus store and date.
  - A storage or perishable change on a known item updates that item (e.g. raspberries → Freezer from now on).
  - The confirmation lists exactly what changed, or warns that nothing did.
- **Pack sizes**:
  - A web search that finds the exact product also reads its pack size ("18 count", "12 oz") and converts it to the item's unit.
  - On review, **"Look up pack sizes online"** fills new packaged items that are still counted as 1.
  - In Needs info, a confirmed match also fixes earlier purchases of that product saved as "1".
  - Products remember `pack_size`, so "I bought the blue eggs" means one full pack (18) next time.
- **Counting in packs (decided):**
  - A bare count of a packaged product means **packs as sold**: "5 crates of Costco water" = 5 × 6 gal = **30 gal**; "2 Costco egg whites" = 2 × (6 cartons in mL).
  - The item is still measured in its basic unit (gal, mL, each, lb). Batches keep `packs` too.
  - An amount with a measuring unit ("2 gallons", "500 ml") or loose things ("6 avocados") is used as said.
  - **A spoken price is per pack** (like a receipt line), so 5 crates at $4.75 = $23.75. It counts as the total only when the user says "total" / "altogether".
  - Different stores are different products: Walmart egg whites have their own pack size (often a single carton), looked up separately.
  - Pack size not known yet: the card shows "N packs · pack size?". The product appears in Needs info, and once its size is found, those purchases become packs × size.
  - The review card shows the math as a badge (`5 × 6 gal`). Changing packs (in Details or by voice) recalculates the amount and price.
  - New liquids are measured in **mL**, except drinking water and milk in **gal**.
- **Receipts reuse your saved products (decided):** the receipt AI sees your known products (with pack sizes and nicknames) and matches lines to them ("KS WATER 40PK" = your saved Kirkland water). A matched line reuses that product, its item and its pack size, so it never shows up in Needs info again.
- **Live progress while reading receipts (decided, Option A):**
  - A step bar: 📷 Photos → 🧾 Items → ✅ Review.
  - Reading a photo: each receipt line pops in as a gray chip as the AI writes it.
  - Working out items: a real progress bar (receipt lines turned into items so far, e.g. "14 of 23 lines") plus green item chips with emojis.
  - Uses Gemini's streaming mode, which returns the same answer in the same time, just piece by piece. Screen updates are limited to 4 a second and can never break or slow the reading. If streaming fails, the app quietly uses the normal call.
- **Deleting never erases what you confirmed (decided):** products and items are never removed by a delete; their info, pack size, barcode, nicknames and photo are reused the next time you buy them.
- **After a lookup, everything follows (decided):**
  - Saving a match always shows **"1 pack = how many {unit}?"**, prefilled from the lookup and editable. It also lists the purchases it will change (e.g. "1 ml → 2,839 ml").
  - Saving updates the product (info, photo, pack size, barcode) and its purchases (amount and unit). Pantry, Insights, lifespans, warnings and best price are all calculated from those rows, so they follow.
  - 🔄 in Needs info: a product with a known pack size whose purchases still say "1" shows old → new amounts, with **Update them** or **They're right as they are**.
  - An amount said in units ("1 ml") of a product with a known pack size is saved as its share of a pack, so it's never mistaken for "1 pack".
- **Mic button**: one big round button. Tap to start, tap the red stop to finish, or press and hold (release stops). No waveform or timer.
- **Undo**: the last **20 changes**. Undo marks rows as undone (never erases them), so there's always a record.

## History & undo (decided)

- **Costco and Costco Business Center are one store, "Costco"** (same products). Web searches check both sites.
- A **"History & undo"** link on Home has two lists:
  - **Recent purchases (last 20)**, each item on its own row with a 🗑 button that deletes **only that item**. Receipts with 2+ items also have **Delete whole receipt**, which asks first.
  - **Every delete is undoable.** A delete marks the batch "deleted" (the row stays in the Sheet) and appears under Recent changes with Undo. Deleted batches are hidden everywhere: Pantry, History, Needs info, stores, voice history.
  - Undoing an older change never brings back a batch that was deleted since.
  - **Recent changes (last 20) with Undo.** Undo marks events undone and restores the batch. The same list appears in Voice update.

- **Finding imported products (decided):**
  - **Barcode:** Open Food Facts first (free). If it doesn't have the barcode (it answers "404 not found"), the app **automatically searches Google for the barcode number**. The match still waits for Yes / Not this in Needs info; in Add purchase it fills the card.
  - **Search text is short:** brand + product + real size, plus the store **only for big chains**: Costco, Sam's Club, Walmart, Target, Kroger, Safeway, Albertsons, Randalls, H-E-B, Whole Foods, Trader Joe's, Aldi, Sprouts, Central Market, Fiesta. Small local stores (e.g. Indian groceries), "1 each" counts and saying-it notes are left out.
  - **Finding the product comes first; label info is a bonus.** A few short searches, anywhere (maker, Amazon, South Asian / East Asian grocery sites). If the brand makes only one version (one turmeric powder), that is the product, with any size difference shown as a badge. "Found, no label info" is a normal result.
- **Store-brand and big-chain products use the store's own page (decided):** for products whose brand implies a store (Kirkland = Costco, Great Value = Walmart…) or that were bought at a big chain, the AI must find the product on that store's own site first (costco.com, walmart.com…) and take the pack exactly as that store sells it (e.g. 2 × 32 oz). It uses other sites only if that page isn't found. Imported / local-store products keep the worldwide search.
- **Only food has a food label (decided):** for Household items (moisturizer, detergent…) and One-time items, the completion bar doesn't count ingredients or nutrition, and the panel hides those sections. The item's kind (🍎 Food / 🧴 Household / 📦 One-time) can be fixed in the kept-in chip; Household items show 🧴 on that chip.
- **Items counted in "each":** a found pack of N units is saved as N each (a 2-pack = 2 each), whatever size each unit is. To track fl oz / oz instead, switch with Measured in.
- **Measured in (built):** the 📦 pack chip has a **Measured in** picker (each, lb, oz, gal, qt, fl oz, ml, loads). Changing it changes how the whole item is measured from now on. Convertible amounts are converted (lb ↔ oz, gal ↔ qt ↔ fl oz ↔ ml). Otherwise (each → fl oz) purchases are recounted as packs × the new pack size, and other products of the item wait for their pack size in Needs info.
- **Redo search can correct a saved pack (built):** when the found pack differs from the saved one, it shows "saved 1 × 3.4 → found 2 × 1.7 fl oz" with **Use the found pack** (ticked). A corrected pack size also recounts purchases bought as whole packs.
- **When Google blocks a lookup (RECITATION) (built):** every lookup (Look it up online, Look up all, barcode search, Redo search) tries the normal one-step search first. If that's blocked, it automatically runs three small searches at the same time (product only, nutrition only as numbers, ingredients as separate items) and combines them into the usual result for Yes / Not this. Repeated ingredients from sub-recipes are dropped (first kept). Cost: a blocked lookup is 4 searches; others stay at 1. Based on the lab result: product ✅, nutrition ✅, ingredients-as-sentence 🚫, ingredients-as-items ✅.
- **📚 Typical nutrition (built):** loose **food** (fruit, vegetables, raw meat, fish) gets typical nutrition from **USDA FoodData Central** (key `USDA_API_KEY` in Streamlit secrets, sent in a header).
  - **Automatically when a purchase is saved**, a few at a time; anything it can't match is skipped quietly.
  - **Matching:** USDA's own order is unreliable (its top hit for "banana raw" is banana powder), so "raw" is preferred and other forms (dried, powder, canned, cooked, juice, frozen…) are pushed down. The item panel has **📚 Typical nutrition (USDA)** / **📚 Change typical match**: pick from the matches, see key values per 100 g, then **Use it**.
  - **Items counted in pieces** store a typical piece weight from USDA portions (1 medium banana ≈ 118 g, editable), so counts can become grams for nutrition totals later.
  - Values are per 100 g, shown with "📚 Typical values from USDA (...)". The ingredient is the item itself. **A label's or product page's nutrition always wins**; typical values never replace it.
  - For loose food, nutrition now counts in the completion bar.
- **🧪 Lookup lab (built, a test bench):** Settings → Lookup lab. Pick a product (Pantry products first); the search text is editable, and the store rule applies. Three buttons each run one focused Google search: **Product only** (name, brand, photo, pack, link), **Nutrition only** (numbers), **Ingredients only**, **Ingredients as separate items** (each ingredient as its own short word, to test whether this avoids the RECITATION block). Each shows ✅ got an answer / not found / 🚫 blocked (RECITATION), the time taken, what came back and the sources. Nothing is saved (calls are logged in ai_log as lab_*). Purpose: find out which parts get blocked before changing the real lookup. First result (bettergoods garlic butter wings, Walmart): product ✅, nutrition ✅, ingredients 🚫 blocked.
- **Prices only come from you (decided):** the price paid is only ever set from your receipt, barcode card or what you said, and edited by you. Online lookups (search, barcode, redo search) never read or change prices; they only fill product details. A pack-size fix changes the amount, so the price *per unit* is recalculated, but the price paid stays.
- **Redo search on the store's site (built):** in the Pantry item panel, products from a big chain or with a store brand get **🔎 Redo search on Costco's site** (or Walmart's, etc.). It searches that store's own site, shows the match with its photo and page link, and lists what it would fill as green "+" chips. **Yes, fill it in** fills only empty fields (serving, ingredients, nutrition, pack size, barcode, link). The photo is replaced only if "Use this photo" is ticked (on by default).
- **Packs are "how many × each" (built):** the lookup reports `pack_count` × `each_in_unit`. Everywhere a pack size is edited (lookup result, item panel) there are two boxes, **How many in the pack** × **Each** (with lb + oz / gal + fl oz), showing "1 pack = 2 × 32 oz = 64 oz". The count is saved (products `pack_count`).
- **Look up all (built):** a button at the top of Needs info searches every product still missing size or label info, **up to 10 at once**, using each product's own search text (including edits).
  - A progress bar shows "7 of 12 searched · ✅ found · ⚠️ not found".
  - Results wait in each product's card (marked ✅, opened) for **Yes / Not this**, exactly like a single lookup. Nothing is saved automatically.
  - Not-found products are marked ⚠️ and can be retried.
- **Scan it: one camera for barcode + label (built):** in Needs info, "Barcode" and "Label photo" are merged into one **Scan it** tab (tabs: Edit search → Scan it).
  - The camera scans barcodes live and has a shutter. A barcode is looked up right away (free database → Google). Shutter photos (1–3) → **Read the label**.
  - Gallery photos work too: one with a barcode is looked up; others are treated as label photos.
  - **Every web or barcode match has "📸 Have the pack? Photograph its label"**, open by default when no label info was found. The label is merged in ("Web search + label photo") and wins for ingredients and nutrition; the name, photo, link and barcode stay.
  - **Labels in any language** are read and written in English.
  - **Per-100 g labels:** amounts are saved as printed (serving "100 g"). **%DV is calculated from US FDA daily values** wherever the label doesn't print it.
  - **Insights → Products:** "Add label photo" on any product without ingredients or nutrition (e.g. matched online earlier).
  - Auto-snapping while rotating the pack: **skipped for now** (tap to snap).
- **Look up all: tick boxes (built).** Every product it would search has a tick box (all ticked by default), plus Tick all / Untick all. The button reads "Look up ticked (17 of 19)" when some are unticked.
- **Needs info is now part of Pantry (decided, option A):** the separate Needs info page is gone. Pantry has **All / ⚠️ Needs info (N)** at the top; Home's "N items need info" link opens that view.
  - An item **needs info** when anything in its completion bar is missing **except expiry** (expiry still counts in the bar, but missing only expiry doesn't put an item here).
  - The Needs info view has **🔎 Look up all** (up to 10 at once) with a tick box on each tile, plus Tick all / Untick all. Each match waits on its tile (✅) and in the item panel for Yes / Not this.
  - The item panel has every tool that was on the Needs info page: **🔎 Look it up online** (or **Redo search on <store>'s site** for chain / store-brand products), editable search words, **📷 Scan** (barcode → lookup, or a label photo), label photos, 🎤, Single ingredient, No nutrition.
- **Food and household apart (decided):** Freezer / Fridge / Open air sections hold **food** only. Non-food items (tissues, body wash…) get their own **🧴 Household** section.
- **Tiles (decided):** every tile is the same size (picture area fixed, so tall and wide photos line up). **Tapping anywhere on a tile, including the picture, opens the item panel.** In the panel, 🔍 next to the picture shows it enlarged.
- **Identify, then search (built):** in the item panel, **📷 Scan** accepts a barcode **or a photo of the front / label**.
  - The barcode (free database, then Google) or the photo (read in any language) gives the **exact name and brand**, e.g. "Shan Chicken White Karahi Masala". The app then **automatically searches the web with that exact name** for the photo, pack size, ingredients and nutrition, and shows one preview.
  - The preview shows **name / brand changes** ("Shan masala → Shan Chicken White Karahi Masala") with **Use the found name / brand** (ticked). Purchases take the new name. **Yes, use it** works whenever anything would change.
  - What the photo read (ingredients, nutrition) **wins** over the web and replaces earlier label info, marked "(from your photo)". Everything else still only fills empty fields. The barcode is kept.
- **Choose a picture (built):** 🖼 next to the picture in the item panel → **Find pictures** shows up to 3 real pictures: the barcode database's front photo, **Open Food Facts photos by brand + name**, the photo that **real product pages** found by a web search declare (og:image), then a generic picture of the food. Image links typed by the AI are a last resort (they usually 404). Every picture is downloaded and **kept as a small copy** (a ~5–10 KB data URI in the Sheet), so it shows on any phone and never breaks later; the same applies to any photo saved with `set_photo`.
- **How each item was looked up is visible (decided):** 🔢 barcode, 📷 photo, 🔤 name. A tile shows 🔢 / 📷 while a scan waits and ✅🔢 / ✅📷 / ✅🔤 once found. The Look up all progress bar counts each method ("🔢 5 · 📷 2 · 🔤 3"), and the preview says "found by its barcode 8964…" / "from your photo" / "by name".
- **Sizes printed in names are used (decided):** after every lookup, "22 oz", "50 g", "2 x 32 oz", "1.7 fl oz, 2-pack" or "12 rolls" in the name, pack text or barcode quantity is read directly (no AI) and converted to the item's unit (g, kg, l included; "oz" on a drink = fl oz). It fills an empty pack size and **wins** when the AI's number disagrees by more than 5%; a wrong saved pack then shows as "saved → found" in the preview.
- **Exact names and their own items (decided):** Pantry tiles and the item panel's title show the **product's exact name** without brand or size ("Chicken White Karahi Masala"), falling back to the item name.
  - When a lookup or scan finds an exact name the generic item doesn't cover ("Spice mix" vs "Chicken White Karahi Masala"; plurals ignored, so "Eggs" vs "Brown Egg" isn't offered), the preview offers **Make it its own item** (name editable; ticked when the item already has other products, otherwise offered unticked). Only this product and its purchases move, so different masalas each learn their own shelf life. An item with that name already existing is joined; the old item is removed only when nothing uses it.
  - The ✏️ popover also has **Item (for shelf life)** + **Move to this item**, to do it by hand anytime.
- **Nutrition is done only by a real choice (decided):** for food, nutrition gets ✓ only with values (label, product page, or accepted 📚 USDA typical) or an explicit **🚫 No nutrition** made with the USDA option beside it (products `nutrition_status` = "chosen", with Undo). Older "No nutrition" / "No label to read" marks, made before USDA existed, no longer count for nutrition. "No label to read" still counts for ingredients.
- **A lookup can replace ingredients / nutrition (decided):** when a search or scan finds ingredients or nutrition different from what's saved (a "Single ingredient" placeholder, USDA typical values, an earlier lookup), the preview shows **saved → found** with **Use the found ingredients / nutrition** (ticked). Real values clear an earlier "No nutrition". Other fields still only fill when empty.
- **"No brand" and "No date printed" (decided):** in the ✏️ name/brand popover, **No brand** (products `no_brand`) marks brand as done and stops lookups from adding one. In the ⏳ expiry chip, **No date printed on it** is saved for the **item** (items `no_expiry`, e.g. all avocados), marks expiry as done and shows "⏳ no date printed"; how long it lasts comes from your own history (Insights).
- **Scan now, look up later (built):** in the item panel, 📷 Scan only **saves** what was scanned, so you can go straight to the next item. A barcode is saved to the product immediately (it survives a reload). Photos are kept in the app's memory until looked up, and are lost if the app fully reloads first.
  - Scanned tiles show 📷. Pantry shows a bubble "📷 N items scanned · M photos". In the All view there's **🔎 Look up scanned (N)**; in Needs info, scanned items join **🔎 Look up all**.
  - **One button, best method per item:** waiting photos (read, then search with the exact name) → barcode, scanned or saved earlier (identify, then search with the exact name) → name search. The button shows the mix ("Look up all (8) · 5 by barcode/photo"). Up to 10 run at once; results wait on tiles (✅) for Yes.
  - The panel still has **Look up now** for a single item, and **Clear**.
- **🛒 Bought is always a count of packs (decided):**
  - 📦 **Pack** = Measured in + Items in one pack × Size of one item (e.g. 12 × 330 ml). 🛒 **Bought** = Packs bought (1, 2…); total = packs × pack.
  - **"Size varies (sold by weight)"** toggle in 📦 for salmon, chicken packs, produce by the pound (products `pack_varies` = "yes"). Those have no fixed pack size; 🛒 asks Packs bought **and "This time it weighed"** (lb + oz), stored on that purchase. It counts as a known pack size in the completion bar and Needs info.
  - Until a pack size is set, 🛒 shows Packs bought plus the amount, with a hint to set 📦.
- **Consistency rules (decided, after a full check):**
  - Every edit box that shows *saved* data (item panel chips, pack, bought, expiry, search words, Insights' Kept in) is tied to the saved value, so it always starts from what's stored, even after a lookup, voice or another screen changed it.
  - Items counted in "each": the pack editor only asks **Items in one pack** (one item = 1 each), so Save always appears.
  - Saving a pack size also records how many packs each earlier purchase was (12 each with a 12-roll pack = 1 pack), so 🛒 reads "1 pack · 12 each".
  - Checked end to end: lookup → pack boxes; kept-in → Pantry section and Insights; voice expiry → panel; packs bought → History; rename → purchases; Finished → Pantry and Home count; Undo → back everywhere.
- **Pack words (decided):** 📦 **pack** = what the store sells as one thing = **Items in one pack** × **Size of one item**. 🛒 **bought** = **Packs bought** × one pack (or just the amount when there's no pack size), editable anytime in the item panel. The review card says "Packs bought".
- **Pantry is a photo grid (decided, option A):** three tiles per row on a phone. Each tile has the photo (or emoji), a short name (tap to open the item panel), a colored days badge (⚠️ when flagged) and a 5-segment completion bar. **Finished** and **Some went bad** live in the item panel (top), along with why it's flagged. The "Remember something?" voice box is a one-line expander, open while an update waits for approval. Freezer / Fridge / Open air groups stay.
- **Pantry item panel (built):** tap an item's name in Pantry to open a **side drawer** (Streamlit dialog, `position="right"`). Tapping outside or ✕ closes it; it stays open while you save things.
  - **Decluttered layout (decided, built):** nothing removed, only regrouped. Top to bottom:
    1. **Header, one row:** picture · name + brand · small 🔍 (big picture) / 🖼 (choose a picture) / ✏️ (name, brand, no brand, own item).
    2. **A lookup result**, when one waits for Yes / Not this, sits right under the header.
    3. **Completion bar** with only the **missing** fields as gray chips, plus one green **✓ N** for what's done.
    4. **Actions, one row:** Finished · Some went bad · 📷 Scan · 🔎 Look up (on <store> for chain / store-brand products) · ✏️ search words. What's scanned and waiting shows below with Look up now / Clear. Why it's flagged (⚠️) sits above.
    5. **One chip row:** 🗓 date · store, 💲 price, ⏱ days open (badges), then tap-to-edit chips ❄️ kept in · ⏳ expiry (perishables) · 🛒 bought · 📦 pack.
    6. **Tabs:** 🧾 Ingredients (chips, 🎤 Say them, 🌿 Single ingredient) | 📊 Nutrition (%DV chart, 📚 USDA typical, 🚫 No nutrition) | 🏷 Nicknames (with barcode and page link). Household / One-time items show only Nicknames.
  - **One camera (decided):** the separate 📸 label cameras under Ingredients and Nutrition are gone; **📷 Scan** reads a barcode, the front, or the ingredients / nutrition panel.
  - **Nutrition** chart: highest first, 🟢 ≤5% low, 🟠 medium, 🔴 ≥20% high (FDA rule of thumb).
  - A lookup that also renames the product keeps the brand it just filled (fixed: the rename used to blank it).
- **Profile completion bar (built):** every Pantry card and the item panel show a bar like a website's profile completion, e.g. ▰▰▰▰▰▱▱▱▱▱ 50%: red under 40%, orange under 80%, green from 80%. The panel shows the missing fields as gray chips and a single green ✓ count for the rest.
  - **Packaged products:** photo, kept in, brand, pack size, ingredients, nutrition (barcode and nicknames do **not** count; nicknames were dropped at the user's request) (so voice understands it next time), plus expiry for perishables.
  - **Loose items:** photo, kept in, plus expiry for perishables.
  - **🌿 Single ingredient** (next to Ingredients when missing): saves the product itself as the only ingredient (e.g. "Turmeric powder") and counts as done.
  - **🚫 No nutrition** (next to Nutrition when missing): you confirm there's nothing worth tracking; it counts as done (products column `nutrition_status` = "none").
- **Panel actions refresh only the panel** (e.g. adding a nickname), so open sections stay open.
- **Amounts in two units (built):** anywhere a weight is typed in **lb** there's an **oz** box beside it, and **gal** gets a **fl oz** box. The small box starts at 0, so 1 lb + 6 oz = 1.375 lb. Amounts display the same way (e.g. "2 lb 5 oz").
- **Merge duplicate products (built):** "Same as a product I have" on every product in Insights → Products, and in Needs info.
  - Pick the right product, see a preview (purchases that move, details and nicknames it brings), then tap Merge.
  - The kept product keeps its own details. Empty ones (photo, info, pack size, barcode…) are filled from the duplicate, and nicknames are combined.
  - All purchases move to it (and to its item). Purchases still saved as "1" get its pack size. The duplicate row is then removed.
- **To-do (later, with the visual Pantry redesign):** one Pantry card per item (e.g. "💧 Purified water · 24 gal · 2 purchases"). Tapping it lists each purchase (date, product, amount), each still with its own Finished / Some went bad, because purchases of the same item can end differently (an unopened older milk stays good while a newer opened one goes bad). Until then: **one card per purchase (decided)**.

## Sheet layout v2 (decided)

- **No stores or purchases tabs.** The store name and receipt info (purchase_id, time, source, receipt total) live on each **batches** row. Rows from the same receipt share a purchase_id.
- AI tabs stay visible (no hiding).
- **Readable IDs**, like company email addresses: first come gets the clean name, later duplicates get -2, -3 …
  - Item `goat-meat`, product `thomas-farms-goat-cubes-15-lb`, batch `goat-meat-2026-10-28`, purchase `costco-2026-10-28`, event `goat-meat-2026-10-28-wasted`, change `change-2026-10-30-1432`.
  - IDs never change, even if a name is edited later.
- Old test data: the user deletes it via History & undo, then removes the old "stores" and "purchases" tabs by hand.

## Product photos & nicknames (decided)

- **Photos**, with no extra AI cost. Shown in Needs info, Insights and Pantry (instead of the emoji).
  - The Open Food Facts photo for barcode scans.
  - A **tiny thumbnail of the user's own label photo**, about 1–5 KB, stored in the Sheet.
  - Best effort: the store page's preview image after a web search. Many store sites block this.
- **Web search links** come from the pages Google actually returned, never a URL the model typed (those could 404).
- **Nicknames** per product, in a `nicknames` column separated by ";":
  - Added by mic or text in Needs info and in Insights → Products. Each one can be removed.
  - **Saved automatically** when the user names a product by voice in their own words and it matches.
  - The voice AI sees every known product with its nicknames and reuses that product (and its last price and size).

## Pantry voice & item pictures (decided)

- **Pantry has its own mic** ("Remember something?") for "three eggs broke", "the broccoli went bad", and so on. It uses the same preview → approve → Undo as Voice update. Voice update stays on Home too.
- **Each item gets its own generic picture** from its English Wikipedia article (free, no AI cost).
  - The AI names the best article ("Catla" for katla fish, "Ilish" for hilsa, "Egg white").
  - Items saved earlier get pictures filled in a few at a time.
  - In Insights → item → **Change picture**, type a better word if two items look alike.
- **Picture order**: the product's own photo → the item's picture → emoji.

## In-app camera (built; needs testing on the Pixel)

- **Barcode: live scanning.** Point the camera; each barcode beeps, vibrates and is added, then scanning continues for the next item.
  - Uses Chrome's built-in barcode reader on Android, else the bundled open-source ZXing library (Apache-2.0, `grocery/camera/`).
- **Receipt: in-app camera** with a big shutter button. Each photo is read right away, as before.
- **Barcode mode is one screen, like Say it.** Each scan becomes a card at once:
  - A known product brings its last price, pack size and storage.
  - A new one gets its name and size from Open Food Facts plus one AI call for item, unit and storage.
  - An unknown one gets an empty card, filled by voice or text.
  - Scanning the same barcode again adds another pack.
  - The "Missed something?" mic and Save/Discard are on the same screen.
- **Missing store**: a red "Store missing" box appears right above Save, pre-filled from store brands (Kirkland → Costco, Great Value → Walmart, Signature Select → Safeway, Member's Mark → Sam's Club, …). The voice fix sets the store from a store brand too.
- **Review cards are compact**: one line per item (name · quantity · price · colored badges · delete). Everything else, including "Is your last X finished?", is under **Details**.
- **No online lookups during review** (they took too long). Lookups live in **Needs info** on Home:
  - It lists packaged products missing 📏 pack size and/or 🧾 ingredients.
  - **One "Look it up online" fills both.**
  - Other options: label photo, barcode, edit the search words, "No label to read", "Size varies / saved right".
- **Nickname mic**: it plainly transcribes what was said (it no longer sees the product name, which made it invent look-alikes). It shows "Heard: …" to confirm before saving.
- Gallery pickers stay available under "Or pick … from your gallery".

## Design principle: visual first (user rule, applies everywhere)

- **Easy for the eyes, intuitive, minimal reading.** Use colors, badges, charts and icons instead of sentences.
  - Example: items with enough data are **green**, items without are **red**, instead of writing "not enough data yet".
- Extra details shown today will be trimmed later. Keep this idea in mind for every screen.

## Phase 4 decisions (Pantry, warnings, Insights)

- **Pantry** shows days open + usual lifespan, **no estimate of how much is left**.
- **Quick buttons per item** in Pantry, as a voice backup:
  - **Finished**: one tap.
  - **Some went bad**: a **free text box** ("200 grams", "one bell pepper", "half"), read by the AI, then previewed and approved. No fixed fraction choices.
- **Insights lists every item**, color-coded by how much data it has.
- A batch finished on the day it was bought counts as lasting 1 day.

## Future: one central voice button (user request)

- Eventually **one big voice button**. The user just talks, and the AI works out the intent:
  - adding purchases
  - updating (finished, wasted, expiry)
  - deleting
  - other
- It **asks follow-up questions** when the user was vague or wasn't heard clearly.
- For now, "Say it" (in Add purchase) and "Voice update" stay separate. They should share one voice engine so merging later is easy.

## Safeguards (decided)

- **No password gate** for now. The app isn't shared with anyone.
- **Item matching**: the AI always matches against the existing item list ("an avocado is an avocado"). A merge tool is available for mistakes.
- **Duplicate receipt warning**: same store + date + total as an earlier receipt.
- **Timezone: America/Chicago (Houston)** for all dates.
- **Model fallback**: model names in Settings, plus a backup model if a call fails.
- **Raw AI reading stored** for every receipt/screenshot/voice note, so mistakes can be traced.
- **Label photo = ground truth.** It overrides Open Food Facts. US labels give per-serving nutrition and servings per container.
- **No separate backup feature.** Google Sheets version history covers accidental bad writes.
- **"Still have these?"** check-ins are folded into the **dashboard**, not a separate feature.
- **Unusual receipt lines**: returns and refunds (negative), coupons on their own line, priced-by-weight items.

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

## Lookup cost (decided, built)
- **Cost facts (checked):** Gemini's Google Search grounding: 5,000 free searches a month, then $14 per 1,000. Each search a lookup runs is billed. Claude Pro ($20) doesn't include API use; Claude's API web search is $10 per 1,000 plus tokens, so switching wouldn't save money.
- **Cheapest first, stop when it's enough** (same steps for every item; only where it stops differs):
  1. **📷 Label photos** → read by the cheap label model (any language). No web search when the photos give everything still missing. The photo of the front becomes the item's picture; the printed net quantity gives the pack size.
  2. **🔢 Barcode** → the free Open Food Facts database. No web search when it gives everything still missing. A barcode it doesn't know costs **one** web search (by the number), not two.
  3. **🔤 Name** → one web search.
  - When Google blocks an answer and it's fetched in parts, only the parts still missing are asked for.
- **Look up all skips** (unless something new was scanned for the item):
  - items a lookup can't help: only a photo, kept in or a date missing (🖼 in the item finds pictures from free sources first); loose food's nutrition comes from 📚 USDA (free).
  - items not found in the last **30 days** (products `lookup_missed_on`); scanning a barcode or saving a find clears it.
  - A caption says how many were skipped and why.
- **Tick all / Untick all** are real buttons next to Look up all (they were hidden behind it by the tick-box style). Only items Look up all will act on get a tick box.
- **What you do:** scan the barcode; for imported (Pakistani, Indian, East Asian) items also photograph the back label and front; then 🔎 Look up all.

## Smoother taps (built)
- Each tap goes to the server and back (Streamlit redraws the page); the server part is fast (~0.1 s for 40 items), so the delay is the round trip.
- **Instant feedback:** a tile presses in the moment it's touched and stays highlighted until the app answers.
- **No lost or double taps:** while the app is working, tiles dim and ignore taps. For 0.8 s after the item panel opens, taps outside it are ignored, so a second tap can't close it again (a small page script, `TAP_JS`).
- **Less to send per tap:** saved pictures are served as small files the phone keeps (`static/pics`, Streamlit static serving) instead of being resent inside every tile on every tap.

## Groups (decided; step 1 built)
- **A group = things you use interchangeably, whatever the brand** (Sausages, Mango, Paratha, Water). Things you don't swap are each their own group even with similar names (each masala, chutney, sauce flavour). In the data a group is the existing **item** level (products sit under it; shelf life is learned per group). Purpose: Insights per group and per product over the year.
- **Step 1 (built):** once per Pantry visit, one cheap AI call (label model) suggests a group for every product that has none (`products.group_suggestion`, asked once per product). The item panel has a **🗂 group chip**: ✨ Use AI's pick (existing or new group), a list of existing groups, ➕ New group, or ✓ Keep. Confirming sets `products.group_status = "ok"`; until then the bar shows **group?** (so Needs info lists them to review). The old "Item (for shelf life)" box in ✏️ moved here. The list shows only groups something is in: a group emptied by a move disappears at once (deleted purchases, kept for Undo, don't count).
- **Step 2, Pantry shows groups (built):** in **All**, purchases of the same group become one stacked tile with **×N** and the most urgent days badge (picture: the group's own, else its first product photo). **Tap** → its items open on their own row right under it, **instantly, on the phone** (the row is always drawn but hidden; no server trip; it stays open across redraws); tap again to close; tap an item to open its panel. **Press and hold** → its items pop up; **slide to one and let go** to open it (a page script that taps hidden per-item buttons). A group of one is just its item tile (one tap opens it). **Needs info** stays one tile per purchase (tick boxes).
- **Next steps (agreed):** 3) New receipts: the AI places new products in a group automatically, changeable. 4) A group's first product photo is its picture in Insights. 5) Merge in Pantry (built): a **Merge** chip in the item panel (likely duplicates — bought the same day — listed first). Open purchases of the merged product bought the same day at the same store combine into one: amounts, packs and prices added; waste recorded on a combined row is rescaled to its share (¼ of one of 2 packs = ⅛); the panel stays on the kept purchase. The same product bought twice the same day at the same store (two tiles, one product) gets **Combine them into one** at the top of the Merge chip.

## Pictures from online orders (built)
- When a receipt is a screenshot of an online order (Walmart, Costco, Instacart…), the reading AI also gives where each item's product picture is (`picture_box`, 0–1000 coordinates). No extra AI call.
- When the review is built, the app cuts each picture out of the screenshot (kept in memory, not in the draft, since a Sheet cell is too small) and shows it on the review card. On save it becomes the new product's photo, or fills a known product's missing photo. Boxes that are too small, too wide or text-shaped are ignored.
- Online orders also give full product names with sizes, so names and pack sizes are more exact. Ingredients and nutrition still come from barcode, label photo or lookup.
- **Paper receipt + app screenshots of the same purchase (built):** each line remembers whether it came from a paper receipt or an app screenshot. When both are present, the AI is told they're two views of the SAME purchase: one item per thing bought, using both lines (the app's full name, size and picture; the price counted once, paper wins if they differ, marked to check).

## Fix a receipt's date (built)
- History: each receipt has a 📅 button to change its date (not in the future). Every item on that receipt moves to the new day, so days open, lifespans and warnings follow.
- A receipt whose date is more than 60 days from the day it was added shows :orange-badge[date looks wrong?] (e.g. read as 2023 instead of 2026).
- **Pictures, made sturdier (built):** each item's picture is cut out right when its screenshot is read and kept on that line (and in the draft when it fits a Sheet cell), so it survives overlaps, reloads and the paper + app matching. Picture boxes are accepted as a list, `{ymin,…}` or pixels. If the AI links an item only to its paper line, the screenshot line with the same price and most of the same words gives its picture. The review shows "📷 N of M item pictures taken from your screenshots".
- **Review warns about odd dates:** a receipt date more than 60 days from today shows a warning before saving.

## Kept in is per product (built)
- **Where it's kept belongs to each product** (products `storage`; empty = its group's usual place). A frozen brand stays in Freezer while a fresh brand of the same group is in Fridge. The ❄️ chip in the item panel moves this product only ("every time you buy it"); "goes bad" and Food/Household stay on the group.
- A product keeps its place when it moves to another group. New products start in their group's usual place; on a receipt, a product bought before defaults to where you keep it.
- **A group split across places shows in each section** with only what's kept there (e.g. "Sausages ×2" in Freezer, "Sausages" in Fridge).
- Insights' "Kept in" shows where the group's products are (lists them when they differ); changing it there moves the whole group. Insights still counts the group as a whole.
- For later (shelf life): learn shelf life per group **and** place.

- The page script that handles taps carries a version number, so an open page switches to a newer copy without a reload.

## Group pictures (built, step 4)
- A group's picture is the first photo of a product in it (your own soda can), else its generic picture, else its emoji. Used by Pantry group tiles and the Insights header.
- Generic Wikipedia pictures are only fetched for groups with no product photo at all.
- Moving a product to another group keeps its picture: loose fruit, whose picture lived on its old single-item group, takes that picture along.

## AI costs (built)
- Every AI call now logs, besides input/output tokens: **thinking tokens** (billed as output; were never recorded), **search-result tokens** the model read (billed as input), the number of **Google searches** it ran (each billed), and its **cost** (ai_log `thought_tokens`, `tool_tokens`, `searches`, `cost_usd`).
- Prices are editable in Settings → AI prices (`ai_prices` "model=in/out" per 1M tokens; `price_per_1000_searches`). Defaults match Google's pricing page (checked Nov 2026): 3.8 Flash $0.75/$3.75 until Dec 31, 2026, then $1.50/$7.50 from Jan 1, 2027 (update the setting then); 3.5 Flash-Lite $0.30/$2.50; Google Search grounding: 5,000 free a month, then $14 per 1,000, billed per search query, capped at 1,500 a day. A model with no price uses the dearest one, so costs aren't understated. Google's free monthly searches aren't subtracted.
- **After each action a 💸 pop-up** says what its AI calls cost, with the number of Google searches and the biggest tasks — also for lookups inside the item panel (which redraw only the panel).
- **Settings → 💸 AI costs:** today / 7 / 30 days / all: total, calls, searches; a bar per kind of task (cost and count); cost per use; cost by day; the last 30 calls. Older rows (before this) lack thinking and searches, so they're marked as low estimates.

## Alcoholic drinks and ABV (built)
- **Spotting them:** the receipt and voice AI mark each new item as an alcoholic drink or not (items `alcohol`; no extra AI call). Items without a mark are judged by well-known words (beer, wine, vodka, hard seltzer, White Claw…; not ginger ale, root beer, non-alcoholic). A **🍷 Alcoholic drink** switch in the ❄️ chip corrects it for the whole group.
- **ABV is needed for them:** the bar shows **ABV?** until known; it counts for Needs info and Look up all. The free barcode database's alcohol % is read; web lookups are told ABV matters most for drinks (also in the split lookup); label photos already read it.
- **🍷 chip** in the item panel: edit the ABV; shows pure alcohol per pack (e.g. 12 × 12 fl oz at 5% = 7.2 fl oz ≈ 12 US standard drinks of 0.6 fl oz) — what Insights will add up later.

## Blocked lookups ask first (built, cut D)
- When Google blocks a lookup's answer (RECITATION: it would copy label text word for word), the app no longer fetches it in 3 parts by itself (that costs about 4 lookups: the blocked call plus 3 full calls, each with its own searches). The item's panel shows 🚧 why, and **🧩 Fetch in 3 parts (≈4× a lookup)** to do it on request. When a barcode or photo already identified the product, that part is still shown and only the label info is marked blocked. Look up all marks blocked items without counting them as "not found".
- Every failed call logs why (ai_log `error`); 💸 AI costs shows how many lookups were **🚧 Blocked** in the period.
- Cost notes (decided, not built): limit thinking only where little reasoning is needed (reading receipt photos and labels, group suggestions, picture search), keeping it for receipt → items, voice and web lookups. A lookup's picture comes from the same search at almost no extra cost; 🖼 Find pictures is a second paid search when free sources fall short.

## Cheaper lookups and the thinking trial (built)
- **Lookups start with 1–2 Google searches** (each search is billed). The item panel's lookup result (found or not found) has **🔎 Search more**: the same lookup with a few searches across more sites, for the odd item the quick one misses; it shows what it cost. The 3-part fetch uses the same setting.
- **🖼 Find pictures stays as is (decided):** a lookup's picture comes from its own search at almost no extra cost; Find pictures tries free sources first and searches the web only when they find fewer than 3.
- **🧪 Thinking trial** (Settings, replaces the Lookup lab): add a long receipt; it's read twice, with the model's normal thinking and with low thinking (Google's `thinkingLevel: low`), and shows each run's cost, item count and total, plus a side-by-side item table (✅ same · ⚠️ different · ❌ only normal · ➕ only low). Nothing is saved. Used to decide whether to limit thinking in the real app. Logged as "🧪 Thinking trial" on the costs page.
- **Find pictures, fixed:** the generic picture is searched by the product's own plain name ("Kirkland Signature Mangoes, 4 lb" → "Mangoes"), not its group ("Fruits"); when Wikipedia says it's busy, the app waits 2 s and tries once more; and the popover lists what was tried ("Tried: 🥫 Open Food Facts: 0 found · 🌐 web search: 1 page, 0 pictures that load (shops often block downloads) · 📚 Wikipedia (Mangoes): found one").
- **Phone-friendly inputs:** keyboard hints ("Press Enter to apply", "Ctrl+Enter") are hidden app-wide. The ✏️ search words box is one line with a **🔎 Search with these words** button (tapping it takes what was typed). The 💸 cost pop-up stays 15 seconds (✕ closes it sooner).
