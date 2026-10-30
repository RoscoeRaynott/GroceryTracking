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
- **Mic button**: one big round button. Tap to start, tap the red stop to finish, or press and hold (release stops). No waveform or timer.
- **Undo**: the last **20 changes**. Undo marks rows as undone (never erases them), so there's always a record.

## History & undo (decided)

- **Costco and Costco Business Center are one store, "Costco"** (same products). Web searches check both sites.
- A **"History & undo"** link on Home has two lists:
  - **Recent purchases (last 20) with Delete.** Delete **erases** the purchase completely: its batches, their events, its AI readings, and any item, product, or store nothing else uses. Meant for test entries.
  - **Recent changes (last 20) with Undo.** Undo marks events undone and restores the batch. The same list appears in Voice update.

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
